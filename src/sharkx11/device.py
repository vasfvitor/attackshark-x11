"""Transport to the mouse: feature reports (settings) and input report 0x03 (events), over ``/dev/hidraw`` on Linux
and ``hidapi`` on Windows."""
import sys
import time

from . import discovery, protocol as p

WINDOWS = sys.platform == "win32"
ACK_TRIES, ACK_WAIT = 10, 0.5        # the app resends every 500 ms until the mouse ACKs, at most 10 times
READ_WAIT = {p.PID_RECEIVER: 0.5, p.PID_WIRED: 0.1}   # pause between the read request and the answer
LIVE_WAIT = 0.01                     # live_reports: wait per source and pass


def open_link(path):
    """The platform's link to the settings interface at ``path``: ``set_feature``, ``get_feature``, ``read``."""
    if WINDOWS:
        from .winhid import WinLink
        return WinLink(path)
    from .hidraw import HidrawLink
    return HidrawLink(path)


def open_live():
    """``[(kind, link)]`` with the reports the GUI follows for live presses: "mouse" (clicks, wheel; Linux only)
    and "vendor" (events, media keys). Empty when no mouse is plugged in."""
    settings = next(discovery.find_devices(), (None,))[0]
    if settings is None:
        return []
    if WINDOWS:
        from .winhid import Handle, live_paths
        sources = [("vendor", Handle, q) for q in live_paths(settings)]
    else:
        from .hidraw import HidrawLink
        mouse = discovery.find_mouse_node(settings)
        sources = [(kind, lambda q: HidrawLink(q, write=False), q)
                   for kind, q in (("mouse", mouse), ("vendor", settings)) if q]
    links = []
    try:
        for kind, opener, path in sources:
            links.append((kind, opener(path)))
    except OSError:
        for _kind, link in links:
            link.close()
        raise
    return links


class Device:
    """The mouse's settings interface. Exits with a message (``SystemExit``) when there is no mouse or no
    permission. ``model`` and ``battery`` (``(charging, percent)``) fill in from the events it sees."""

    def __init__(self, path=None):
        devs = list(discovery.find_devices())
        if path:
            devs = [d for d in devs if d[0] == path] or [(path, p.PID_RECEIVER)]
        if not devs:
            sys.exit("no ATTACK SHARK receiver or mouse found (is the receiver plugged in?)")
        self.path, self.pid = devs[0]
        try:
            self.link = open_link(self.path)
        except PermissionError:
            sys.exit(f"permission denied on {self.path}: install udev/70-attackshark-x11.rules (see README)")
        except OSError as e:
            sys.exit(f"cannot open the mouse: {e}")
        self.model = self.battery = None

    def close(self):
        self.link.close()

    # ------------------------------------------------------------ raw I/O
    def set_feature(self, data):
        self.link.set_feature(data)

    def get_feature(self, rid, size):
        return self.link.get_feature(rid, size)

    def events(self, timeout):
        """Yield ``(event, v0, v1)`` for every input report 0x03 within ``timeout`` seconds (also updates
        ``model`` and ``battery``)."""
        end = time.time() + timeout
        while (left := end - time.time()) > 0:
            r = self.link.read(left)
            if r is None:
                return
            ev = p.parse_event(r)
            if ev:
                model, event, v0, v1 = ev
                self.model = model
                if event == p.EV_BATTERY:
                    self.battery = (v0 == p.CHARGING, v1)
                yield event, v0, v1

    def _wait(self, seconds):
        for _ in self.events(seconds):
            pass

    # ------------------------------------------------------------ settings
    def read(self, rid):
        """One settings report, by the app's handshake: request 0xA0, check its answer, then fetch."""
        size = p.SIZES[rid]
        for _ in range(3):
            self.set_feature(p.read_request(rid, 0 if rid == p.IDENT else 1))
            self._wait(READ_WAIT[self.pid])
            if self.get_feature(p.READ_REQ, p.SIZES[p.READ_REQ])[1] == 1:
                r = self.get_feature(rid, size)
                if r[0] == rid and r[1] == size:
                    return r
        raise IOError(f"no answer to the read of report {rid:#04x} (is the mouse on?)")

    def write(self, data):
        """Send one settings report; through the receiver, resend until the mouse ACKs (``03 <model> 50 00``)."""
        if data[0] not in p.WRITABLE or len(data) != p.SIZES[data[0]]:
            raise ValueError(f"refusing to send report {data[0]:#04x} ({len(data)} bytes)")
        for _ in range(ACK_TRIES):
            self.set_feature(data)
            if self.pid == p.PID_WIRED:          # on the cable the feature write itself is the confirmation
                return
            for event, v0, _v1 in self.events(ACK_WAIT):
                if event == p.EV_ACK:
                    if v0 != 0:
                        raise IOError(f"mouse rejected report {data[0]:#04x} (status {v0})")
                    return
        raise IOError(f"no confirmation for report {data[0]:#04x} (is the mouse on?)")

    def identify(self, timeout=3.0):
        """``(model id, version)``: the model from the mouse's events (it sends a heartbeat every 2 s; ``None``
        if none arrives), the version from bytes 2-3 of report 0x0B (``01 12`` on the X11 -> "1.12")."""
        r = self.read(p.IDENT)
        if self.model is None:
            next(self.events(timeout), None)          # any event names the model
        return self.model, f"{r[2]:x}.{r[3]:02x}"

    def wait_battery(self, timeout=6.0):
        """The battery state, waiting for the mouse's heartbeat if none was seen yet."""
        if self.battery is None:
            for event, _v0, _v1 in self.events(timeout):
                if event == p.EV_BATTERY:
                    break
        return self.battery
