"""Windows transport through ``hidapi``. Windows splits interface 2 into top-level collections, each its own
device path: the feature reports (settings) go to the usage-page-0x0B collection, the input reports 0x03
(events) come from the 0x0A one and the media keys (report 0x02) from the consumer (0x0C) one.

The receiver's interface 2 enumerates as ``...&MI_02&Col01#8&2d75ef4f&0&0000#{guid}`` to ``...&Col04#...&0003#...``:
collections of one device share the path once the ``&ColNN`` and the trailing ``&NNNN`` are dropped.
"""
import re

from . import protocol as p

SETTINGS_PAGE, EVENTS_PAGE, CONSUMER_PAGE = 0x0B, 0x0A, 0x0C


def _hid():
    import hid        # the hidapi package: only needed (and only a dependency) on Windows
    return hid


def _key(path):
    return re.sub(r"&col[0-9a-f]+#(.*)&[0-9a-f]+#", r"#\1#", path.lower())


def _collections():
    return [d for d in _hid().enumerate(p.VID)
            if d["product_id"] in p.PIDS and (d["usage"] == 0 or d["usage_page"] == CONSUMER_PAGE)]


def find_devices():
    """``(path, pid)`` of every receiver/wired mouse settings collection, receiver first."""
    found = [(d["path"].decode(), d["product_id"]) for d in _collections() if d["usage_page"] == SETTINGS_PAGE]
    return sorted(found, key=lambda f: f[1] != p.PID_RECEIVER)


def sibling(path, page):
    """The path of the ``page`` collection of the same device as the collection at ``path``, or ``None``."""
    return next((d["path"].decode() for d in _collections()
                 if d["usage_page"] == page and _key(d["path"].decode()) == _key(path)), None)


class Handle:
    """One open collection, for input reports."""

    def __init__(self, path):
        self.h = _hid().device()
        try:
            self.h.open_path(path.encode())
        except OSError as e:
            raise OSError(f"could not open {path} ({e})") from None
        self.h.set_nonblocking(True)

    def close(self):
        self.h.close()

    def read(self, timeout):
        """The next input report, or ``None`` if none arrives within ``timeout`` seconds."""
        ms = int(timeout * 1000)
        r = self.h.read(64, ms) if ms > 0 else self.h.read(64)
        return bytes(r) or None


class WinLink:
    """The settings collection at ``path`` for the feature reports, plus its events collection for the input
    reports, as one link."""

    def __init__(self, path):
        events = sibling(path, EVENTS_PAGE)
        if events is None:
            raise OSError(f"no events collection next to {path}")
        self.feat = Handle(path)
        try:
            self.events = Handle(events)
        except OSError:
            self.feat.close()
            raise

    def close(self):
        self.feat.close()
        self.events.close()

    def set_feature(self, data):
        if self.feat.h.send_feature_report(bytes(data)) < 0:
            raise OSError(f"feature report {data[0]:#04x} not sent")

    def get_feature(self, rid, size):
        r = bytes(self.feat.h.get_feature_report(rid, size))
        if not r:
            raise OSError(f"feature report {rid:#04x} not received")
        return r

    def read(self, timeout):
        return self.events.read(timeout)


def live_paths(path):
    """The collections with the reports the GUI follows for live presses: events (DPI button) and consumer
    (media keys). The mouse collection (clicks, wheel) stays with Windows, which opens it exclusively."""
    return [q for q in (sibling(path, EVENTS_PAGE), sibling(path, CONSUMER_PAGE)) if q]
