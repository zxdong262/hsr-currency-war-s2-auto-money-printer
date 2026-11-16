"""Global shortcuts that keep working while a game has focus.

Three backends, each covering a failure mode of the others:

``register``  Win32 ``RegisterHotKey`` - a real OS hotkey, no focus dependency.
              Fails when another app already owns the combination.
``poll``      ``GetAsyncKeyState`` polling - reads the physical key state, so it
              still fires when a game swallows normal key messages (raw input /
              exclusive fullscreen) or when the hook is blocked by UIPI.
``pynput``    low-level keyboard hook - fallback on non-Windows platforms.

All of them funnel into :meth:`HotkeyManager._fire`, which de-duplicates by
action name, so one key press never starts the bot twice.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional

from .logbus import LOG, event

IS_WINDOWS = sys.platform == "win32"

MOD_VK = {
    "ctrl": 0x11, "control": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B, "cmd": 0x5B, "super": 0x5B,
}

NAMED_VK = {
    "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "ins": 0x2D,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "pause": 0x13, "printscreen": 0x2C, "capslock": 0x14,
}

# RegisterHotKey modifier flags
MOD_FLAG = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}
MOD_NOREPEAT = 0x4000


def _main_vk(name: str) -> Optional[int]:
    name = name.strip().strip("<>").lower()
    if not name:
        return None
    if name in NAMED_VK:
        return NAMED_VK[name]
    if name.startswith("f") and name[1:].isdigit():
        n = int(name[1:])
        if 1 <= n <= 24:
            return 0x70 + n - 1
    if len(name) == 1:
        ch = name.upper()
        if "A" <= ch <= "Z" or "0" <= ch <= "9":
            return ord(ch)
    return None


def parse_spec(spec: str) -> Optional[dict]:
    """``"<ctrl>+<alt>+s"`` -> {"vk": 0x53, "mods": [0x11, 0x12], ...}."""
    if not spec:
        return None
    parts = [p.strip() for p in str(spec).split("+") if p.strip()]
    if not parts:
        return None
    mods: list[str] = []
    for p in parts[:-1]:
        key = p.strip("<>").lower()
        if key not in MOD_VK:
            LOG.warning("unknown modifier %r in hotkey %r", p, spec)
            return None
        if key not in mods:
            mods.append(key)
    vk = _main_vk(parts[-1])
    if vk is None:
        LOG.warning("unknown key %r in hotkey %r", parts[-1], spec)
        return None
    flags = 0
    for m in mods:
        flags |= MOD_FLAG[m]
    return {
        "spec": spec,
        "vk": vk,
        "mods": [MOD_VK[m] for m in mods],
        "flags": flags,
        "has_mod": bool(mods),
    }


class HotkeyManager:
    def __init__(self, hotkeys: dict[str, str], on_start: Callable, on_stop: Callable,
                 on_panic: Callable, on_toggle: Optional[Callable] = None,
                 poll_ms: int = 40, dedupe_s: float = 0.35):
        self.spec = {k: v for k, v in dict(hotkeys).items()
                     if k in ("start", "stop", "panic", "toggle") and v}
        self._callbacks: dict[str, Callable] = {}
        for name, fn in (("start", on_start), ("stop", on_stop),
                         ("panic", on_panic), ("toggle", on_toggle)):
            if fn is not None:
                self._callbacks[name] = fn
        self.poll_interval = max(0.01, poll_ms / 1000.0)
        self.dedupe_s = dedupe_s
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._listener = None
        self._last: dict[str, float] = {}
        self._backends: list[str] = []
        self._registered: list[str] = []
        self._enabled = False

    # ------------------------------------------------------------- backends --

    def start(self) -> bool:
        with self._lock:
            self.stop_locked()
            self._stop = threading.Event()
            self._backends = []
            bindings: dict[str, dict] = {}
            for name, spec in self.spec.items():
                if name not in self._callbacks:
                    continue
                parsed = parse_spec(spec)
                if parsed:
                    bindings[name] = parsed
                else:
                    event("hotkey ignored (unparsable)", action="hotkeys", level=30,
                          name=name, key=spec)

            if not bindings:
                LOG.warning("no usable hotkeys; use the web UI buttons")
                self._enabled = False
                return False

            if IS_WINDOWS:
                self._start_register(bindings)
                self._start_poll(bindings)
                # polling alone is enough; register is the nicer path
                if not self._backends:
                    self._start_pynput(bindings)
            else:
                self._start_pynput(bindings)

            self._enabled = bool(self._backends)
            event("hotkeys registered", action="hotkeys", keys=self.spec,
                  backends=self._backends, registered=self._registered)
            return self._enabled

    def _start_register(self, bindings: dict[str, dict]) -> None:
        ready = threading.Event()
        t = threading.Thread(target=self._register_worker, args=(bindings, ready),
                             daemon=True, name="hotkey-register")
        t.start()
        self._threads.append(t)
        if ready.wait(timeout=1.5) and self._registered:
            self._backends.append("register")

    def _register_worker(self, bindings: dict[str, dict], ready: threading.Event) -> None:
        u = ctypes.windll.user32
        msg = wintypes.MSG()
        u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # create the thread queue
        ids: dict[int, str] = {}
        try:
            for idx, (name, hk) in enumerate(bindings.items()):
                hid = 0xB000 + idx
                # NOREPEAT stops a held key from re-firing; ignore it if the OS rejects it
                mods = hk["flags"] | MOD_NOREPEAT
                if u.RegisterHotKey(None, hid, mods, hk["vk"]):
                    ids[hid] = name
                else:
                    LOG.warning("RegisterHotKey failed for %s (%s) - polling will cover it",
                                name, hk["spec"])
        finally:
            self._registered = list(ids.values())
            ready.set()

        try:
            while not self._stop.is_set():
                got = False
                while u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                    got = True
                    if msg.message == 0x0312:  # WM_HOTKEY
                        name = ids.get(int(msg.wParam))
                        if name:
                            self._fire(name, "register")
                if not got:
                    self._stop.wait(0.02)
        finally:
            for hid in ids:
                u.UnregisterHotKey(None, hid)

    def _start_poll(self, bindings: dict[str, dict]) -> None:
        t = threading.Thread(target=self._poll_worker, args=(bindings,),
                             daemon=True, name="hotkey-poll")
        t.start()
        self._threads.append(t)
        self._backends.append("poll")

    def _poll_worker(self, bindings: dict[str, dict]) -> None:
        u = ctypes.windll.user32
        held: dict[str, bool] = {}
        while not self._stop.is_set():
            for name, hk in bindings.items():
                down = all(u.GetAsyncKeyState(v) & 0x8000 for v in hk["mods"]) \
                    and bool(u.GetAsyncKeyState(hk["vk"]) & 0x8000)
                if down and not held.get(name, False):
                    self._fire(name, "poll")
                held[name] = down
            self._stop.wait(self.poll_interval)

    def _start_pynput(self, bindings: dict[str, dict]) -> None:
        try:
            from pynput import keyboard
        except Exception as exc:
            LOG.warning("pynput unavailable (%s)", exc)
            return
        mapping = {hk["spec"]: (lambda n=name: self._fire(n, "pynput"))
                   for name, hk in bindings.items()}
        try:
            self._listener = keyboard.GlobalHotKeys(mapping)
            self._listener.start()
            self._backends.append("pynput")
        except Exception as exc:
            LOG.warning("pynput hotkeys unavailable (%s)", exc)
            self._listener = None

    # -------------------------------------------------------------- firing ---

    def _fire(self, name: str, backend: str) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self._last.get(name, 0.0) < self.dedupe_s:
                return
            self._last[name] = now
        event("HOTKEY", action="hotkey", name=name, key=self.spec.get(name), backend=backend)
        fn = self._callbacks.get(name)
        if fn is None:
            return
        try:
            fn()
        except Exception:
            LOG.exception("hotkey %s failed", name)

    # ------------------------------------------------------------- lifecycle --

    def stop_locked(self) -> None:
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
            self._listener = None
        for t in self._threads:
            if t.is_alive() and t is not threading.current_thread():
                t.join(timeout=1.0)
        self._threads = []
        self._enabled = False
        self._backends = []
        self._registered = []

    def stop(self) -> None:
        with self._lock:
            self.stop_locked()

    def reload(self, hotkeys: dict[str, str]) -> bool:
        with self._lock:
            self.spec = {k: v for k, v in dict(hotkeys).items()
                         if k in ("start", "stop", "panic", "toggle") and v}
        return self.start()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def backends(self) -> list[str]:
        return list(self._backends)

    @property
    def registered(self) -> list[str]:
        return list(self._registered)
