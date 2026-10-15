"""The Linux transport: hidraw feature ioctls and input reads (a socketpair stands in for the node)."""
import socket
import sys

import pytest

if sys.platform == "win32":
    pytest.skip("hidraw is Linux only", allow_module_level=True)

from sharkx11 import hidraw  # noqa: E402


def test_hidraw_link(monkeypatch):
    host, dev = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    flags = []
    monkeypatch.setattr(hidraw.os, "open", lambda path, f: flags.append((path, f)) or host.fileno())
    calls = []

    def ioctl(fd, req, buf):
        nr, size = req & 0xFF, req >> 16 & 0x3FFF
        assert fd == host.fileno() and size == len(buf) and req >> 8 & 0xFF == ord("H") and req >> 30 == 3
        calls.append((nr, bytes(buf)))
        if nr == 0x07:
            buf[1] = 0x55
    monkeypatch.setattr(hidraw.fcntl, "ioctl", ioctl)
    link = hidraw.HidrawLink("/dev/hidraw5")
    hidraw.HidrawLink("/dev/hidraw4", write=False)
    assert flags == [("/dev/hidraw5", hidraw.os.O_RDWR | hidraw.os.O_NONBLOCK),
                     ("/dev/hidraw4", hidraw.os.O_RDONLY | hidraw.os.O_NONBLOCK)]
    link.set_feature(bytes([0xA0, 4, 56, 0, 1, 0, 0, 0]))
    assert link.get_feature(0x0B, 8) == bytes([0x0B, 0x55, 0, 0, 0, 0, 0, 0])
    assert calls == [(0x06, bytes([0xA0, 4, 56, 0, 1, 0, 0, 0])), (0x07, bytes([0x0B] + [0] * 7))]
    assert link.read(0) is None
    dev.send(bytes([3, 0x55, 0x40, 1, 80]))
    assert link.read(-1) == bytes([3, 0x55, 0x40, 1, 80])
    link.close()
    dev.close()
