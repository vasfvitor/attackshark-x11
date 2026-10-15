"""x11tray: the battery watcher, the icon, autostart and the tray menu (pystray, winreg and kernel32 stand-ins)."""
import os
import sys
import threading
import types

import pytest

from sharkx11 import discovery, protocol as p, state, tray, winhid
from sharkx11.gui import i18n, theme


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    i18n.LANG = "pt"
    yield tmp_path
    i18n.LANG = "pt"


# ------------------------------------------------------------ watch
class Events:
    def __init__(self, *reports):
        self.reports, self.closed = list(reports), False

    def read(self, timeout):
        if not self.reports:
            raise OSError("unplugged")
        r = self.reports.pop(0)
        if callable(r):
            return r()
        return r

    def close(self):
        self.closed = True


def test_watch(monkeypatch):
    stop, shown, clock = threading.Event(), [], [100.0]
    monkeypatch.setattr(tray.time, "time", lambda: clock[0])

    def later(seconds):
        clock[0] += seconds
    links = []

    def handle(path):
        assert path == "col3"
        links.append(Events(bytes([3, 0x55, p.EV_BATTERY, 1, 62]), bytes([3, 0x55, p.EV_DPI, 2, 0]),
                            lambda: later(tray.OFFLINE_S + 1), bytes([3, 0x55, p.EV_BATTERY, p.CHARGING, 63])))
        return links[-1]
    plugged = [[("col4", p.PID_RECEIVER)], [("col4", p.PID_RECEIVER)], [], [("col4", p.PID_RECEIVER)]]

    def devices():
        if not plugged:
            stop.set()
            return iter([])
        return iter(plugged.pop(0))
    monkeypatch.setattr(discovery, "find_devices", devices)
    monkeypatch.setattr(winhid, "sibling", lambda path, page: {"col4": "col3"}.get(path))
    monkeypatch.setattr(winhid, "Handle", handle)
    monkeypatch.setattr(stop, "wait", lambda s: None)
    tray.watch(stop, shown.append)
    assert shown == [(False, 62), tray.ASLEEP, (True, 63), None,          # unplugged: the read failed
                     (False, 62), tray.ASLEEP, (True, 63), None,
                     None,                                                # nothing plugged in
                     (False, 62), tray.ASLEEP, (True, 63), None, None]
    assert all(link.closed for link in links)


def test_watch_stops_while_listening(monkeypatch):
    stop = threading.Event()
    link = Events(lambda: stop.set())
    monkeypatch.setattr(discovery, "find_devices", lambda: iter([("col4", p.PID_RECEIVER)]))
    monkeypatch.setattr(winhid, "sibling", lambda path, page: "col3")
    monkeypatch.setattr(winhid, "Handle", lambda path: link)
    tray.watch(stop, lambda st: None)
    assert link.closed


# ------------------------------------------------------------ icon and tooltip
def test_render_and_tooltip():
    def corner(st, last=None):
        img = tray.render(st, last)
        assert img.size == (tray.SIZE, tray.SIZE)
        return "#%02x%02x%02x" % img.getpixel((tray.SIZE // 2, 3))[:3]
    assert corner((False, 62)) == theme.ACCENT and corner((False, 30)) == theme.WARN
    assert corner((False, 15)) == theme.DANGER and corner((True, 5)) == theme.ACCENT
    assert corner(tray.ASLEEP, 62) == corner(None) == theme.MUTED
    charging, not_charging = tray.render((True, 100), None), tray.render((False, 100), None)
    assert charging.getpixel((tray.SIZE // 2, tray.SIZE - 5)) != not_charging.getpixel((tray.SIZE // 2, tray.SIZE - 5))
    assert tray.tooltip((False, 62), None) == "ATTACK SHARK X11 — 62%"
    assert tray.tooltip((True, 63), None) == "ATTACK SHARK X11 — 63%, carregando"
    assert tray.tooltip(tray.ASLEEP, None) == "ATTACK SHARK X11 — mouse dormindo ou desligado"
    i18n.LANG = "en"
    assert tray.tooltip(tray.ASLEEP, 62) == "ATTACK SHARK X11 — mouse asleep or off (last reading: 62%)"
    assert tray.tooltip(None, 62) == "ATTACK SHARK X11 — receiver not found"


def test_font_fallback(monkeypatch):
    from PIL import ImageFont

    real = ImageFont.truetype

    def no_segoe(font, *a, **kw):
        if font == "segoeuib.ttf":
            raise OSError(font)
        return real(font, *a, **kw)
    monkeypatch.setattr(ImageFont, "truetype", no_segoe)
    assert tray.font(20) is not None


# ------------------------------------------------------------ autostart and single instance
class Reg:
    HKEY_CURRENT_USER, KEY_SET_VALUE, REG_SZ = "HKCU", 2, 1

    def __init__(self):
        self.values = {}

    def OpenKey(self, root, key, reserved=0, access=0):
        assert (root, key) == ("HKCU", tray.RUN_KEY)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def QueryValueEx(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name], self.REG_SZ

    def SetValueEx(self, k, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, k, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


@pytest.fixture
def reg(monkeypatch):
    r = Reg()
    monkeypatch.setitem(sys.modules, "winreg", r)
    return r


def test_autostart(reg, monkeypatch, tmp_path):
    scripts, bindir = tmp_path / "Scripts", tmp_path / "py"
    scripts.mkdir()
    bindir.mkdir()
    monkeypatch.setattr(tray.sysconfig, "get_path", lambda name: str(scripts))
    monkeypatch.setattr(tray.sys, "executable", str(bindir / "python.exe"))
    assert tray.autostart_command().startswith(f'"{bindir / "python.exe"}" -c "import sys; sys.path.insert(0, r')
    (bindir / "pythonw.exe").write_text("")
    cmd = tray.autostart_command()
    root = os.path.dirname(os.path.dirname(tray.__file__))
    assert cmd == f'"{bindir / "pythonw.exe"}" -c "import sys; sys.path.insert(0, r\'{root}\'); ' \
                  'from sharkx11.tray import main; main()"'
    (scripts / "x11tray.exe").write_text("")
    assert tray.autostart_command() == f'"{scripts / "x11tray.exe"}"'
    assert not tray.autostart_enabled()
    tray.set_autostart(True)
    assert tray.autostart_enabled() and reg.values[tray.RUN_NAME] == f'"{scripts / "x11tray.exe"}"'
    tray.set_autostart(False)
    tray.set_autostart(False)                                      # already off: nothing to delete
    assert not tray.autostart_enabled()


def test_first_instance(monkeypatch):
    errors = [0, 183]
    kernel32 = types.SimpleNamespace(CreateMutexW=lambda sec, own, name: name)
    monkeypatch.setattr(tray.ctypes, "WinDLL", lambda name, use_last_error: kernel32, raising=False)
    monkeypatch.setattr(tray.ctypes, "get_last_error", lambda: errors.pop(0), raising=False)
    assert tray.first_instance() and tray._mutex == "Local\\sharkx11-tray"
    assert not tray.first_instance()


def test_prefs(config):
    assert tray.load_json(tray.prefs_path()) == {}
    tray.save_prefs({"autostart": False})
    assert tray.load_json(tray.prefs_path()) == {"autostart": False}
    (config / "sharkx11/tray.json").write_text("[1]")
    assert tray.load_json(tray.prefs_path()) == {}
    (config / "sharkx11/tray.json").write_text("{")
    assert tray.load_json(tray.prefs_path()) == {}
    os.remove(config / "sharkx11/tray.json")
    os.rmdir(config / "sharkx11")
    (config / "sharkx11").write_text("")                           # unwritable: ignored
    tray.save_prefs({"autostart": True})


# ------------------------------------------------------------ tray
class Icon:
    def __init__(self, name, icon, title, menu):
        self.name, self.icon, self.title, self.menu = name, icon, title, menu
        self.visible, self.stopped = False, False

    def run(self, setup):
        setup(self)

    def stop(self):
        self.stopped = True


PYSTRAY = types.SimpleNamespace(Icon=Icon, Menu=lambda *items: list(items),
                                MenuItem=lambda text, action, checked=None: types.SimpleNamespace(
                                    text=text, action=action, checked=checked))


def test_tray(monkeypatch, reg):
    monkeypatch.setitem(sys.modules, "pystray", PYSTRAY)
    started = []
    monkeypatch.setattr(tray, "watch", lambda stop, show: started.append((stop, show)))
    t = tray.Tray()
    assert t.icon.title == "ATTACK SHARK X11 — mouse dormindo ou desligado"
    t.run()
    assert t.icon.visible
    for _i in range(50):
        if started:
            break
        threading.Event().wait(0.01)
    assert started and started[0][0] is t.stop
    first = t.icon.icon
    t.show(tray.ASLEEP)                                            # unchanged: nothing redrawn
    assert t.icon.icon is first
    t.show((False, 62))
    t.show(tray.ASLEEP)
    assert t.last == 62 and t.icon.title.endswith("(última leitura: 62%)")
    autostart, quit_ = t.icon.menu
    assert autostart.text == "Iniciar com o Windows" and not autostart.checked(autostart)
    autostart.action()
    assert autostart.checked(autostart) and tray.load_json(tray.prefs_path()) == {"autostart": True}
    autostart.action()
    assert not autostart.checked(autostart) and tray.load_json(tray.prefs_path()) == {"autostart": False}
    quit_.action()
    assert t.stop.is_set() and t.icon.stopped


def test_main(monkeypatch, reg, config):
    monkeypatch.setattr(tray, "WINDOWS", False)
    with pytest.raises(SystemExit, match="Windows"):
        tray.main()
    monkeypatch.setattr(tray, "WINDOWS", True)
    monkeypatch.setattr(tray, "first_instance", lambda: False)
    tray.main()                                                    # another tray runs: quit at once
    ran = []
    monkeypatch.setattr(tray, "first_instance", lambda: True)
    monkeypatch.setattr(tray, "Tray", lambda: types.SimpleNamespace(run=lambda: ran.append(i18n.LANG)))
    monkeypatch.setattr(i18n, "system_lang", lambda: "en")
    tray.main()
    assert ran == ["en"] and tray.RUN_NAME in reg.values          # first start: autostart on
    os.makedirs(state.config_dir(), exist_ok=True)
    with open(os.path.join(state.config_dir(), "gui.json"), "w") as f:
        f.write('{"lang": "pt"}')
    tray.save_prefs({"autostart": False})
    reg.values.clear()
    tray.main()
    assert ran == ["en", "pt"] and not reg.values                  # turned off in the menu: left off
