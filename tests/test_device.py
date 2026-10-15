import sys
import types

import pytest

from sharkx11 import config, device, discovery, protocol as p


def test_open_errors(monkeypatch):
    monkeypatch.setattr(discovery, "find_devices", lambda: iter([]))
    with pytest.raises(SystemExit, match="no ATTACK SHARK"):
        device.Device()
    monkeypatch.setattr(discovery, "find_devices", lambda: iter([("/dev/x", p.PID_RECEIVER)]))

    def fails(error):
        def open_link(path):
            raise error
        return open_link
    monkeypatch.setattr(device, "open_link", fails(PermissionError()))
    with pytest.raises(SystemExit, match="70-attackshark-x11.rules"):
        device.Device("/dev/other")
    monkeypatch.setattr(device, "open_link", fails(OSError("could not open x")))
    with pytest.raises(SystemExit, match="cannot open the mouse: could not open x"):
        device.Device()


def test_read_identify_and_events(fake):
    d = device.Device(fake.path)
    assert d.path == fake.path and d.pid == p.PID_RECEIVER
    fake.event(p.EV_BATTERY, 3, 87)
    fake.send([0x02, 0xE9, 0x00])               # a consumer-key report is not an event
    assert d.identify() == (0x55, "1.12") and d.battery == (False, 84)       # the read's heartbeat is newer
    assert config.decode_dpi(d.read(p.DPI))["active"] == 2
    assert fake.log[-1] == p.read_request(p.DPI)
    fake.refuse_reads = 1                                     # first handshake refused, second accepted
    assert d.read(p.POLLING)[3] == 1
    fake.refuse_reads = 5
    with pytest.raises(IOError, match="0x06"):
        d.read(p.POLLING)
    d.close()


def test_identify_without_events(fake):
    fake.heartbeat = None
    d = device.Device()
    assert d.identify(timeout=0.05) == (None, "1.12")


def test_read_checks_the_answer(fake):
    d = device.Device()
    fake.reports[p.POLLING] = bytes([6, 8, 1, 1, 0xFE, 0, 0, 0, 0])      # wrong length byte
    with pytest.raises(IOError):
        d.read(p.POLLING)


def test_write_waits_for_ack(fake):
    d = device.Device()
    d.write(config.encode_polling(250))
    assert fake.reports[p.POLLING][3] == 4 and d.model == 0x55
    fake.status = 2
    with pytest.raises(IOError, match="rejected"):
        d.write(config.encode_polling(250))
    fake.status, fake.silent = 0, True
    fake.event(p.EV_DPI, 2)                                 # unrelated events do not count as an ACK
    with pytest.raises(IOError, match="no confirmation"):
        d.write(config.encode_polling(250))
    assert len([r for r in fake.log if r[0] == p.POLLING]) == 2 + device.ACK_TRIES


def test_write_refuses_unknown_reports(fake):
    d = device.Device()
    for data in (bytes([0x0C, 0x0A, 1, 0xFE, 1, 0xFE, 0, 0, 0, 0]), bytes([0x06, 9, 1, 1, 0xFE, 0, 0, 0])):
        with pytest.raises(ValueError, match="refusing"):
            d.write(data)
    assert not fake.log


def test_wired_write_needs_no_ack(fake):
    fake.pid = p.PID_WIRED
    d = device.Device()
    fake.silent = True
    d.write(config.encode_buttons(config.DEFAULT_MAP))
    assert fake.buttons[3:6] == bytes([2, 0, 0])


def test_wait_battery(fake):
    d = device.Device()
    assert d.wait_battery(0.05) is None and list(d.events(0)) == []
    fake.event(p.EV_DPI, 1)
    fake.event(p.EV_BATTERY, 1, 64)
    assert d.wait_battery(1) == (False, 64)
    assert d.wait_battery(0) == (False, 64)                 # already known: no wait


class FakeHidraw:
    def __init__(self, path, write=True):
        if path == "/dev/locked":
            raise PermissionError
        self.path, self.write, self.closed = path, write, False

    def close(self):
        self.closed = True


def test_linux_links(monkeypatch):
    monkeypatch.setattr(device, "WINDOWS", False)
    monkeypatch.setitem(sys.modules, "sharkx11.hidraw", types.SimpleNamespace(HidrawLink=FakeHidraw))
    assert device.open_link("/dev/hidraw5").write
    monkeypatch.setattr(discovery, "find_devices", lambda: iter([("/dev/hidraw5", p.PID_RECEIVER)]))
    monkeypatch.setattr(discovery, "find_mouse_node", lambda path: "/dev/hidraw4")
    links = device.open_live()
    assert [(k, link.path, link.write) for k, link in links] == [("mouse", "/dev/hidraw4", False),
                                                                 ("vendor", "/dev/hidraw5", False)]
    monkeypatch.setattr(discovery, "find_mouse_node", lambda path: None)          # wired: no mouse node found
    assert [k for k, _link in device.open_live()] == ["vendor"]
    opened = []
    monkeypatch.setattr(discovery, "find_mouse_node", lambda path: "/dev/hidraw4")
    monkeypatch.setattr(discovery, "find_devices", lambda: iter([("/dev/locked", p.PID_RECEIVER)]))
    monkeypatch.setattr(FakeHidraw, "close", lambda self: opened.append(self.path))
    with pytest.raises(PermissionError):
        device.open_live()
    assert opened == ["/dev/hidraw4"]                       # the mouse node was closed again
