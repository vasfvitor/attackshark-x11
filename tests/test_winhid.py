"""The Windows transport against a stand-in for the hidapi module (collections as the receiver enumerates them)."""
import sys
import types

import pytest

from sharkx11 import device, protocol as p, winhid

GUID = "#{4d1e55b2-f16f-11cf-88cb-001111000030}"


def col(pid, mi, n, page, usage, inst="8&2d75ef4f&0"):
    path = f"\\\\?\\HID#VID_1D57&PID_{pid:04X}&MI_{mi:02d}&Col{n:02d}#{inst}&{n - 1:04d}{GUID}"
    return dict(product_id=pid, interface_number=mi, usage_page=page, usage=usage, path=path.encode())


RECEIVER = [col(0xFA60, 2, 1, 0x01, 0x80), col(0xFA60, 2, 2, 0x0C, 0x01), col(0xFA60, 2, 3, 0x0A, 0),
            col(0xFA60, 2, 4, 0x0B, 0)]
WIRED = [col(0xFA55, 2, 3, 0x0A, 0, "9&1&0"), col(0xFA55, 2, 4, 0x0B, 0, "9&1&0")]
OTHER = [col(0x1234, 2, 4, 0x0B, 0)]
FEAT, EVENTS, CONSUMER = (RECEIVER[i]["path"].decode() for i in (3, 2, 1))


class FakeDevice:
    opened = {}

    def __init__(self):
        self.inbox, self.sent, self.path, self.blocking = [], [], None, True

    def open_path(self, path):
        if path.decode() in HID.refuse:
            raise OSError("open failed")
        self.path = path.decode()
        FakeDevice.opened[self.path] = self

    def set_nonblocking(self, on):
        self.blocking = not on

    def close(self):
        FakeDevice.opened.pop(self.path, None)

    def read(self, size, timeout_ms=0):
        assert size == 64 and not self.blocking
        self.last_timeout = timeout_ms
        return self.inbox.pop(0) if self.inbox else []

    def send_feature_report(self, data):
        self.sent.append(data)
        return -1 if data[0] == 0xEE else len(data)

    def get_feature_report(self, rid, size):
        return [] if rid == 0xEE else [rid, size] + [0] * (size - 2)


HID = types.SimpleNamespace(device=FakeDevice, refuse=set(), devices=RECEIVER + WIRED + OTHER,
                            enumerate=lambda vid: HID.devices if vid == p.VID else [])


@pytest.fixture(autouse=True)
def hid(monkeypatch):
    monkeypatch.setitem(sys.modules, "hid", HID)
    FakeDevice.opened.clear()
    HID.refuse, HID.devices = set(), RECEIVER + WIRED + OTHER
    yield HID


def test_find_devices_and_siblings():
    assert winhid.find_devices() == [(FEAT, p.PID_RECEIVER), (WIRED[1]["path"].decode(), p.PID_WIRED)]
    assert winhid.sibling(FEAT, winhid.EVENTS_PAGE) == EVENTS
    assert winhid.sibling(WIRED[1]["path"].decode(), winhid.CONSUMER_PAGE) is None
    assert winhid.live_paths(FEAT) == [EVENTS, CONSUMER]
    assert winhid.live_paths(WIRED[1]["path"].decode()) == [WIRED[0]["path"].decode()]


def test_link(monkeypatch):
    link = winhid.WinLink(FEAT)
    assert set(FakeDevice.opened) == {FEAT, EVENTS}
    link.set_feature(bytearray([p.READ_REQ, 4, 56, 0, 1, 0, 0, 0]))
    assert FakeDevice.opened[FEAT].sent == [bytes([p.READ_REQ, 4, 56, 0, 1, 0, 0, 0])]
    with pytest.raises(OSError, match="0xee not sent"):
        link.set_feature(bytes([0xEE, 0]))
    assert link.get_feature(p.POLLING, 9) == bytes([p.POLLING, 9] + [0] * 7)
    with pytest.raises(OSError, match="0xee not received"):
        link.get_feature(0xEE, 8)
    FakeDevice.opened[EVENTS].inbox.append([3, 0x55, p.EV_ACK, 0, 0])
    assert link.read(0.5) == bytes([3, 0x55, p.EV_ACK, 0, 0])
    assert FakeDevice.opened[EVENTS].last_timeout == 500
    assert link.read(0) is None and FakeDevice.opened[EVENTS].last_timeout == 0
    link.close()
    assert not FakeDevice.opened


def test_link_open_errors(hid):
    hid.devices = [RECEIVER[3]]
    with pytest.raises(OSError, match="no events collection"):
        winhid.WinLink(FEAT)
    hid.devices = RECEIVER
    hid.refuse = {EVENTS}
    with pytest.raises(OSError, match="could not open .*Col03.*open failed"):
        winhid.WinLink(FEAT)
    assert not FakeDevice.opened                                  # the settings collection was closed again


def test_device_and_live_on_windows(monkeypatch, hid):
    monkeypatch.setattr(device, "WINDOWS", True)
    monkeypatch.setattr(device.discovery, "WINDOWS", True)
    d = device.Device()
    assert d.path == FEAT and isinstance(d.link, winhid.WinLink)
    d.close()
    links = device.open_live()
    assert [(k, link.h.path) for k, link in links] == [("vendor", EVENTS), ("vendor", CONSUMER)]
    for _k, link in links:
        link.close()
    hid.refuse = {CONSUMER}
    with pytest.raises(OSError):
        device.open_live()
    assert not FakeDevice.opened                                  # the events collection was closed again
    hid.devices = []
    assert device.open_live() == []
