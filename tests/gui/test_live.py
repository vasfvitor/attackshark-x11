"""Live presses: report parsing, mapping functions to parts, the animation and the reader thread."""
from sharkx11 import config, device, protocol as p
from sharkx11.gui import live, theme

from .conftest import clear_timers


def test_parse_report():
    assert live.parse_report("mouse", bytes([0x21, 0, 0, 0, 0, 0, 0])) == [("buttons", 0x01)]
    assert live.parse_report("mouse", bytes([0, 0, 0, 0, 0, 0xFF, 0])) == [("buttons", 0), ("wheel", -1)]
    assert live.parse_report("mouse", bytes([0, 0, 0, 0, 0, 1, 0])) == [("buttons", 0), ("wheel", 1)]
    assert live.parse_report("mouse", bytes([4])) == [("buttons", 4)]
    assert live.parse_report("vendor", bytes([2, 0xE9, 0])) == [("consumer", 0xE9)]
    assert live.parse_report("vendor", bytes([3, 0x55, p.EV_DPI, 3, 0])) == [("dpi", 3)]
    assert live.parse_report("vendor", bytes([3, 0x55, p.EV_BATTERY, 1, 80])) == []
    assert live.parse_report("vendor", bytes([1, 0x81])) == []


def fills(app):
    return {k: app.canvas.itemcget(app.parts[k], "fill") for k in list(config.BUTTONS) + ["dpi"]}


def test_clicks_light_the_mapped_parts(app):
    app.buttons = list(config.DEFAULT_MAP)
    app.buttons[7] = "volume_up"                                   # back side button sends Volume +
    app.live("buttons", 0x01 | 0x10)                               # left + forward
    assert app.pressed == {"left", "forward"}
    app.animate()
    f = fills(app)
    assert f["left"] == f["forward"] == theme.ACCENT and f["right"] == theme.BG
    app.live("consumer", 0xE9)
    assert app.pressed == {"left", "forward", "back"}
    app.live("consumer", 0)
    app.live("buttons", 0)                                          # released: fades back
    assert not app.pressed
    for k in app.press_t:
        app.press_t[k] -= theme.PULSE / 2
    app.animate()
    assert fills(app)["left"] not in (theme.ACCENT, theme.SEL_FILL)   # mid-fade
    for k in app.press_t:
        app.press_t[k] -= theme.PULSE
    app.animate()
    f = fills(app)
    assert f["left"] == theme.SEL_FILL and f["forward"] == theme.BG   # selected part keeps its tint
    clear_timers(app)


def test_wheel_and_dpi_flash(app):
    app.live("wheel", -1)
    app.animate()
    assert fills(app)["middle"] != theme.BG
    app.live("dpi", 4)                                             # stage changed on the mouse
    assert app.dpi["active"] == 4 and app.v["active"].get() == 4 and not app.dirty
    app.live("dpi", 4)
    app.live("dpi", 9)                                             # out of range: ignored
    app.animate()
    assert fills(app)["dpi"] != theme.BG
    app.live("buttons", 0x04)                                      # middle click wins over the wheel flash
    app.animate()
    assert fills(app)["middle"] == theme.ACCENT
    clear_timers(app)


def test_live_before_a_read(mk):
    a = mk(read=False)
    a.live("dpi", 2)
    a.live("buttons", 1)
    assert not a.pressed


class Link:
    """A live source: hands out its reports, then fails like an unplugged receiver."""

    def __init__(self, *reports):
        self.reports, self.closed, self.waits = list(reports), False, []

    def read(self, timeout):
        self.waits.append(timeout)
        if not self.reports:
            raise OSError("unplugged")
        return self.reports.pop(0)

    def close(self):
        self.closed = True


def test_reader_thread(app, monkeypatch):
    mouse = Link(bytes([1, 0, 0, 0, 0, 0, 0]), bytes([0, 0, 0, 0, 0, 0xFF, 0]), None)
    vendor = Link(bytes([2, 0xE9, 0]), None)
    monkeypatch.setattr(device, "open_live", lambda: [("mouse", mouse), ("vendor", vendor)])
    got, sleeps = [], []
    monkeypatch.setattr(app, "after", lambda ms, fn, *args: got.append(args))

    def stop(s):
        sleeps.append(s)
        app._closing = True
    monkeypatch.setattr(live.time, "sleep", stop)
    app._live_loop()
    assert got == [("buttons", 1), ("buttons", 0), ("wheel", -1), ("consumer", 0xE9)]
    assert mouse.waits[:3] == [device.LIVE_WAIT, 0, 0] and mouse.closed and vendor.closed and sleeps == [1]
    app._closing = False
    monkeypatch.setattr(device, "open_live", lambda: [])                 # nothing plugged: waits and retries
    app._live_loop()
    app._closing = False

    def open_fails():
        raise OSError("could not open")
    monkeypatch.setattr(device, "open_live", open_fails)
    app._live_loop()
    app._closing = False
    quiet = Link()
    quiet.read = lambda timeout: setattr(app, "_closing", True)          # window closed while listening
    monkeypatch.setattr(device, "open_live", lambda: [("vendor", quiet)])
    app._live_loop()
    assert quiet.closed
    app._closing = False


def test_reader_stops_when_closing(app):
    app._closing = True
    app._live_loop()                                               # returns at once
    app._closing = False
