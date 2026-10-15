"""Emulator of the X11 behind its receiver, wired into sharkx11.device in place of the real link (hidraw node or
Windows collections): feature reports go through ``set_feature``/``get_feature``, input reports (events) come out
of a queue through ``read``.
"""
import queue

from sharkx11 import config, device, discovery, protocol as p


class FakeMouse:
    def __init__(self, pid=p.PID_RECEIVER, model=p.MODEL_X11):
        self.pid, self.model = pid, model
        self.path = "/dev/hidraw-fake"
        dpi = config.encode_dpi(config.default_dpi())
        light = config.encode_light(config.default_light())
        self.reports = {p.IDENT: bytes([p.IDENT, 8, 0x01, 0x12, 0, 0, 0, 0]), p.DPI: dpi, p.LIGHT: light,
                        p.POLLING: config.encode_polling(1000)}
        self.buttons = None            # last button map written
        self.pending = None            # report asked for with 0xA0
        self.log = []                  # every feature report set
        self.silent = False            # never ACK writes
        self.status = 0                # ACK status byte
        self.refuse_reads = 0          # answer the next N read requests with 0xA0 byte1 = 0
        self.heartbeat = (1, 84)       # battery heartbeat sent with each read request (the X11 sends one every
                                       # 2 s); None = silent mouse
        self.inbox = queue.Queue()     # input reports waiting to be read

    def install(self, monkeypatch):
        monkeypatch.setattr(discovery, "find_devices", lambda: iter([(self.path, self.pid)]))
        real = device.open_link
        monkeypatch.setattr(device, "open_link", lambda path: self if path == self.path else real(path))
        return self

    def close(self):
        pass                           # Device.close() closes its link: the emulator stays usable

    def send(self, report):
        """Send an input report to the host."""
        self.inbox.put(bytes(report))

    def event(self, ev, v0=0, v1=0, model=None):
        """Send an input report 0x03 to the host."""
        self.send([0x03, self.model if model is None else model, ev, v0, v1])

    def read(self, timeout):
        try:
            return self.inbox.get(timeout=timeout) if timeout > 0 else self.inbox.get_nowait()
        except queue.Empty:
            return None

    def set_feature(self, data):
        data = bytes(data)
        self.log.append(data)
        if data[0] == p.READ_REQ:
            self.pending = data[1]
            if self.heartbeat:
                self.event(p.EV_BATTERY, *self.heartbeat)
            return
        if data[0] == p.BUTTONS:
            self.buttons = data
        else:
            self.reports[data[0]] = data
        if self.pid == p.PID_RECEIVER and not self.silent:
            self.event(p.EV_ACK, self.status)

    def get_feature(self, rid, size):
        if rid == p.READ_REQ:
            ok = self.pending is not None and not self.refuse_reads
            self.refuse_reads = max(self.refuse_reads - 1, 0)
            return bytes([p.READ_REQ, int(ok)] + [0] * (size - 2))
        assert size == p.SIZES[rid]
        return self.reports[rid]
