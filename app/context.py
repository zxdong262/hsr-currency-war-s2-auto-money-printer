"""Shared runtime context: bot, screen capture, hotkeys and config.

No HTTP / Flask dependency lives here, so the native GUI and the command line
can use exactly the same runtime as the legacy web console.
"""
from __future__ import annotations

import glob
import os
import threading
from typing import Optional

from .bot import Bot, Runner, State
from .config import Config
from .display import describe as describe_display, set_resolution
from .hotkeys import HotkeyManager
from .logbus import event
from .vision import ImageScreen, MoneyReader, TemplateStore, create_screen
from .window import activate_window, find_window, foreground_title, is_elevated, window_elevated


class Context:
    def __init__(self, config: Config, log_dir: str, root: str):
        self.config = config
        self.log_dir = log_dir
        self.root = root
        cfg = config.get()
        self.screen = create_screen(cfg)
        self.store = TemplateStore(cfg, root)
        self.reader = MoneyReader(cfg, self.store)
        self.runner = Runner(config, self.screen, self.store, self.reader, log_dir)
        self.hotkeys = HotkeyManager(
            cfg["hotkeys"], self.hotkeys_start,
            self.runner.stop, self.runner.panic, self.hotkeys_toggle,
            poll_ms=int(cfg["behavior"].get("hotkey_poll_ms", 40)))
        self._lock = threading.RLock()
        self.runtime = None  # filled by main.py with frozen/app paths

    # ------------------------------------------------------------- hotkeys --

    def hotkeys_start(self) -> None:
        self.runner.start(trigger="hotkey")

    def hotkeys_toggle(self) -> None:
        if self.runner.s.running:
            self.runner.stop()
        else:
            self.runner.start(trigger="hotkey")

    def boot(self) -> None:
        self.hotkeys.start()

    def shutdown(self) -> None:
        self.hotkeys.stop()
        self.screen.close()

    # ---------------------------------------------------------------- paths --

    def screenshot_files(self) -> list[str]:
        d = self.config.get()["screenshot_dir"]
        d = d if os.path.isabs(d) else os.path.join(self.root, d)
        return [os.path.basename(p) for p in sorted(glob.glob(os.path.join(d, "*.png")))]

    def frame_for(self, source: Optional[str]):
        if source and source != "live":
            d = self.config.get()["screenshot_dir"]
            d = d if os.path.isabs(d) else os.path.join(self.root, d)
            path = os.path.join(d, os.path.basename(source))  # basename prevents traversal
            if os.path.isfile(path):
                return ImageScreen(path).grab()
        return self.screen.grab()

    # --------------------------------------------------------------- config --

    def rebuild_screen(self, cfg: Optional[dict] = None) -> tuple[bool, str]:
        """Recreate the capturer after a resolution/capture change."""
        with self._lock:
            try:
                new_screen = create_screen(cfg or self.config.get())
                old_screen, self.screen = self.screen, new_screen
                self.runner.screen = new_screen
                old_screen.close()
                return True, ""
            except Exception as exc:
                event("capture rebuild failed", action="config", level=40, error=str(exc))
                return False, str(exc)

    def apply_config(self, patch: dict) -> dict:
        old = self.config.get()
        new = self.config.update(patch)
        with self._lock:
            templ_changed = (
                old["templates"] != new["templates"]
                or old["templates_dir"] != new["templates_dir"]
                or old["match"] != new["match"]
                or old.get("display") != new.get("display")
            )
            if templ_changed:
                self.store.cfg = new
                self.store.ref_height = float(
                    (new.get("display") or {}).get("ref_height", 1440))
                self.store.reload()
                self.reader.cfg = new["money"]
            if old["hotkeys"] != new["hotkeys"]:
                self.hotkeys.reload(new["hotkeys"])
            if old.get("monitor") != new.get("monitor") or old.get("region") != new.get("region") \
                    or old.get("capture") != new.get("capture"):
                if self.runner.s.running:
                    event("screen capture change deferred until run finishes",
                          action="config", level=30)
                else:
                    self.rebuild_screen(new)
        return new

    # -------------------------------------------------------------- display --

    def change_resolution(self, width: int, height: int, confirmed: bool = False) -> dict:
        """Switch display mode; callers must have asked the user first."""
        cfg = self.config.get()
        opts = cfg.get("display") or {}
        if opts.get("ask_before_change", True) and not confirmed:
            return {
                "need_confirm": True,
                "message": f"Switch the screen from {describe_display(cfg)['current']} "
                           f"to {width}x{height}? The game may need a moment to resize.",
            }
        ok, message = set_resolution(width, height)
        if ok:
            if self.runner.s.running:
                event("DISPLAY CHANGE will take effect after the run stops",
                      action="display", level=30)
            else:
                self.rebuild_screen(cfg)
        return {"ok": ok, "message": message, "display": describe_display(cfg)}

    # ---------------------------------------------------------- diagnostics --

    def snapshot(self) -> dict:
        s = self.runner.status()
        s["hotkeys_enabled"] = self.hotkeys.enabled
        s["hotkey_backends"] = self.hotkeys.backends
        s["hotkey_registered"] = self.hotkeys.registered
        s["foreground"] = foreground_title()
        start_cfg = (self.config.get()["behavior"] or {}).get("start") or {}
        win = find_window(start_cfg.get("activate_window") or "")
        s["game_window"] = ({"title": win["title"], "rect": list(win["rect"]),
                             "minimized": win["minimized"]} if win else None)
        s["elevated"] = bool(is_elevated())
        s["game_elevated"] = window_elevated(start_cfg.get("activate_window") or "") \
            if start_cfg.get("activate_window") else None
        s["capture"] = type(self.screen).__name__
        if s["game_elevated"] and not s["elevated"]:
            s["warning"] = ("the game runs as administrator but this app does not - "
                            "synthetic clicks into it are discarded by Windows. "
                            "Restart the app and accept the UAC prompt.")
        try:
            s["monitors"] = self.screen.monitors()
        except Exception:
            s["monitors"] = []
        s["display"] = describe_display(self.config.get())
        return s

    def focus_game(self) -> tuple[bool, str]:
        title = (self.config.get()["behavior"].get("start") or {}).get("activate_window")
        if not title:
            return False, "behavior.start.activate_window is not set"
        ok, why = activate_window(title)
        event("WINDOW FOCUS", action="focus", title=title, ok=ok, result=why,
              foreground_before=foreground_title(), level=20 if ok else 30)
        return ok, why
