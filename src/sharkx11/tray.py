"""``x11tray`` (Windows): the mouse's battery in the notification area.

A separate, read-only process: it follows the receiver's events collection for the battery heartbeat (every 2 s
while the mouse is awake) and never writes to the mouse. It starts with Windows (``HKCU\\...\\Run``) unless that
is turned off in its menu.
"""
import ctypes
import json
import os
import sys
import sysconfig
import threading
import time

from . import discovery, protocol as p, state, winhid
from .gui import i18n, theme
from .gui.i18n import _

WINDOWS = sys.platform == "win32"
OFFLINE_S = 12                   # no heartbeat for this long = mouse asleep/off
ASLEEP = "asleep"
SIZE = 64
RUN_KEY, RUN_NAME = r"Software\Microsoft\Windows\CurrentVersion\Run", "sharkx11 tray"
LOW, HALF = 15, 30               # battery % at or below: red, yellow


def prefs_path():
    return os.path.join(state.config_dir(), "tray.json")


def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_prefs(data):
    try:
        os.makedirs(os.path.dirname(prefs_path()), exist_ok=True)
        with open(prefs_path(), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


# ------------------------------------------------------------ the battery
def watch(stop, show):
    """Until ``stop`` is set, call ``show`` with ``(charging, percent)`` on each heartbeat, ``ASLEEP`` when none
    came for ``OFFLINE_S`` and ``None`` while no receiver or mouse is plugged in. Reconnects every second."""
    while not stop.is_set():
        link = None
        try:
            path = next(discovery.find_devices(), (None,))[0]
            events = path and winhid.sibling(path, winhid.EVENTS_PAGE)
            if not events:
                show(None)
            else:
                link = winhid.Handle(events)
                seen = time.time()
                while not stop.is_set():
                    ev = p.parse_event(link.read(1.0) or b"")
                    if ev and ev[1] == p.EV_BATTERY:
                        seen = time.time()
                        show((ev[2] == p.CHARGING, ev[3]))
                    elif time.time() - seen > OFFLINE_S:
                        show(ASLEEP)
        except OSError:
            show(None)
        finally:
            if link:
                link.close()
        stop.wait(1)


# ------------------------------------------------------------ the icon
def color(st):
    if not isinstance(st, tuple):
        return theme.MUTED
    charging, pct = st
    return theme.ACCENT if charging or pct > HALF else theme.WARN if pct > LOW else theme.DANGER


def font(size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype("segoeuib.ttf", size)
    except OSError:
        return ImageFont.load_default(size)


def render(st, last):
    """The icon: the percentage on a rounded tile coloured by level (grey while asleep or not found)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=SIZE // 5, fill=color(st))
    pct = st[1] if isinstance(st, tuple) else last if st == ASLEEP else None
    text = "–" if pct is None else str(pct)
    d.text((SIZE / 2, SIZE / 2), text, fill=theme.BG, font=font(34 if len(text) > 2 else 44), anchor="mm")
    if isinstance(st, tuple) and st[0]:                       # charging: a bar along the bottom
        d.rectangle((SIZE // 4, SIZE - 7, SIZE * 3 // 4, SIZE - 4), fill=theme.BG)
    return img


def tooltip(st, last):
    if st is None:
        text = _("receptor não encontrado")
    elif st == ASLEEP:
        text = _("mouse dormindo ou desligado") + ("" if last is None else _(" (última leitura: {}%)").format(last))
    else:
        text = f"{st[1]}%" + (_(", carregando") if st[0] else "")
    return "ATTACK SHARK X11 — " + text


# ------------------------------------------------------------ Windows integration
def autostart_command():
    """The command line that starts the tray: the installed ``x11tray.exe``, else this module through pythonw."""
    exe = os.path.join(sysconfig.get_path("scripts"), "x11tray.exe")
    if os.path.exists(exe):
        return f'"{exe}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return (f'"{pyw if os.path.exists(pyw) else sys.executable}" -c '
            f'"import sys; sys.path.insert(0, r\'{root}\'); from sharkx11.tray import main; main()"')


def autostart_enabled():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
        return True
    except OSError:
        return False


def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass


_mutex = None


def first_instance():
    """Hold a named mutex: ``False`` if another tray already holds it."""
    global _mutex
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _mutex = kernel32.CreateMutexW(None, False, "Local\\sharkx11-tray")
    return ctypes.get_last_error() != 183                      # ERROR_ALREADY_EXISTS


# ------------------------------------------------------------ the tray icon
class Tray:
    def __init__(self):
        import pystray
        self.state, self.last = ASLEEP, None
        self.stop = threading.Event()
        menu = pystray.Menu(
            pystray.MenuItem(_("Iniciar com o Windows"), self.toggle_autostart, checked=lambda _i: autostart_enabled()),
            pystray.MenuItem(_("Sair"), self.quit))
        self.icon = pystray.Icon("sharkx11", render(self.state, None), tooltip(self.state, None), menu)

    def show(self, st):
        if st == self.state:
            return
        if isinstance(st, tuple):
            self.last = st[1]
        self.state = st
        self.icon.icon = render(st, self.last)
        self.icon.title = tooltip(st, self.last)

    def toggle_autostart(self, *_a):
        on = not autostart_enabled()
        set_autostart(on)
        save_prefs({**load_json(prefs_path()), "autostart": on})

    def quit(self, *_a):
        self.stop.set()
        self.icon.stop()

    def run(self):
        self.icon.run(setup=self._setup)

    def _setup(self, icon):
        icon.visible = True
        threading.Thread(target=watch, args=(self.stop, self.show), daemon=True).start()


def main():
    if not WINDOWS:
        sys.exit("x11tray is for Windows")
    if not first_instance():
        return
    lang = load_json(os.path.join(state.config_dir(), "gui.json")).get("lang")
    i18n.LANG = lang if lang in i18n.LANGS else i18n.system_lang()
    if load_json(prefs_path()).get("autostart", True):
        set_autostart(True)                                    # also refreshes the path after a move
    Tray().run()

