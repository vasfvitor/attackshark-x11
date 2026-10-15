"""Linux transport: one ``/dev/hidraw`` node carries the feature reports (settings) and the input reports (events)."""
import fcntl
import os
import select


def _ioc(nr, size):
    return (3 << 30) | (size << 16) | (ord("H") << 8) | nr        # _IOC(_IOC_READ|_IOC_WRITE, 'H', nr, size)


class HidrawLink:
    """An open hidraw node. Opening raises ``PermissionError`` without the udev rule."""

    def __init__(self, path, write=True):
        self.fd = os.open(path, (os.O_RDWR if write else os.O_RDONLY) | os.O_NONBLOCK)

    def close(self):
        os.close(self.fd)

    def set_feature(self, data):
        fcntl.ioctl(self.fd, _ioc(0x06, len(data)), bytearray(data))

    def get_feature(self, rid, size):
        buf = bytearray(size)
        buf[0] = rid
        fcntl.ioctl(self.fd, _ioc(0x07, size), buf)
        return bytes(buf)

    def read(self, timeout):
        """The next input report, or ``None`` if none arrives within ``timeout`` seconds."""
        if not select.select([self.fd], [], [], max(timeout, 0))[0]:
            return None
        return os.read(self.fd, 64)
