"""All mouse/keyboard side effects live here, so dry-run and stop are airtight."""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

from .logbus import event
from .vision import Frame, TemplateStore


class StopRequested(Exception):
    pass


class Actions:
    def __init__(self, cfg: dict, stop_event: threading.Event, dry_run: bool = True,
                 log_dir: str = "logs"):
        self.cfg = cfg
        self.timing = cfg["timing"]
        self.stop_event = stop_event
        self.dry_run = bool(dry_run)
        # set by the offline simulator: dry-run, but the fake screen *does* react
        self.simulated = False
        self.debug_dir = os.path.join(log_dir, "debug")
        self.debug_images = bool(cfg.get("debug_images", True))
        self._seq = 0
        self._lock = threading.Lock()
        self._pg = None
        try:
            import pyautogui
            pyautogui.FAILSAFE = True       # yank mouse to a screen corner = emergency stop
            pyautogui.PAUSE = 0
            self._pg = pyautogui
        except Exception as exc:  # headless / no display
            event("pyautogui unavailable, real input disabled", level=30, error=str(exc))

    # --------------------------------------------------------------- helpers --

    def check(self) -> None:
        if self.stop_event.is_set():
            raise StopRequested()

    def sleep(self, ms: float) -> None:
        """Interruptible sleep: the stop hotkey takes effect within ~50 ms."""
        end = time.monotonic() + max(0.0, ms) / 1000.0
        while True:
            self.check()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.05, left))

    def _require_pg(self):
        if self._pg is None:
            raise RuntimeError("no mouse backend available (pyautogui failed to load)")
        return self._pg

    def screenshot(self, tag: str, frame: Frame, store: TemplateStore,
                   matches: Optional[list] = None, extra_crop=None) -> Optional[str]:
        """Persist an annotated frame for post-mortem debugging."""
        if not self.debug_images:
            return None
        try:
            os.makedirs(self.debug_dir, exist_ok=True)
            with self._lock:
                self._seq += 1
                name = f"{int(time.time() * 1000)}-{self._seq}-{tag}.png"
            import cv2
            img = store.annotate(frame, matches or [])
            cv2.imwrite(os.path.join(self.debug_dir, name), img)
            self._prune_debug_images()
            return name
        except Exception as exc:
            event("debug screenshot failed", level=30, error=str(exc))
            return None

    def _prune_debug_images(self) -> None:
        """Keep logs/debug bounded: the bot cycles for hours and saves ~8 frames each."""
        keep = int(self.cfg.get("debug_keep_files", 400) or 0)
        if keep <= 0:
            return
        with self._lock:
            self._since_prune = getattr(self, "_since_prune", 0) + 1
            if self._since_prune < 40:
                return
            self._since_prune = 0
        try:
            names = [n for n in os.listdir(self.debug_dir) if n.endswith(".png")]
            if len(names) <= keep:
                return
            paths = [os.path.join(self.debug_dir, n) for n in names]
            paths.sort(key=lambda p: os.path.getmtime(p))
            for p in paths[:len(paths) - keep]:
                base = os.path.basename(p)
                # only ever touch frames this app wrote: "<epoch_ms>-<seq>-<tag>.png"
                if len(base) > 20 and base.split("-")[0].isdigit():
                    os.remove(p)
        except Exception as exc:
            event("debug prune failed", level=30, error=str(exc))

    # --------------------------------------------------------------- actions --

    def click(self, x: int, y: int, label: str = "", score: float | None = None,
              wait_ms: float | None = None, style: str = "click") -> None:
        """Click at (x, y).

        ``style`` matters when the click opens a modal that appears under the
        cursor: the panel may open on mouse-down and then the mouse-up lands on
        the modal backdrop, which closes it again.  ``away`` releases the button
        a bit higher up so the release cannot hit the backdrop, ``hold`` keeps
        the button down for a moment first.
        """
        self.check()
        event("CLICK", action="click", label=label, x=int(x), y=int(y),
              score=None if score is None else round(float(score), 4),
              style=style, dry_run=self.dry_run)
        if not self.dry_run:
            pg = self._require_pg()
            try:
                if style == "away":
                    lift = int(self.timing.get("release_lift_px", 260))
                    pg.moveTo(int(x), int(y))
                    pg.mouseDown()
                    self.sleep(70)
                    pg.moveTo(int(x), max(0, int(y) - lift))
                    self.sleep(70)
                    pg.mouseUp()
                elif style == "into_panel":
                    # release inside the modal (its title bar) instead of on the
                    # backdrop that closes it
                    pt = (self.cfg.get("behavior") or {}).get("market_open", {}).get("panel_point") \
                        or [1280, 110]
                    px, py = int(pt[0]), int(pt[1])
                    pg.moveTo(int(x), int(y))
                    pg.mouseDown()
                    self.sleep(int(self.timing.get("hold_ms", 160)))
                    pg.moveTo((int(x) + px) // 2, (int(y) + py) // 2)
                    self.sleep(40)
                    pg.moveTo(px, py)
                    self.sleep(40)
                    pg.mouseUp()
                elif style == "hold":
                    pg.moveTo(int(x), int(y))
                    pg.mouseDown()
                    self.sleep(int(self.timing.get("hold_ms", 160)))
                    pg.mouseUp()
                else:
                    pg.moveTo(int(x), int(y))
                    pg.click()
            except Exception as exc:
                if "FailSafe" in type(exc).__name__:
                    self.stop_event.set()
                    raise StopRequested() from exc
                raise
            finally:
                self.release()
        self.sleep(self.timing["after_click_ms"] if wait_ms is None else wait_ms)

    def click_match(self, m, label: str = "", wait_ms: float | None = None,
                    style: str = "click") -> None:
        self.click(m.x, m.y, label or m.name, getattr(m, "score", None), wait_ms, style)

    def drag(self, x1: int, y1: int, x2: int, y2: int, label: str = "") -> None:
        self.check()
        event("DRAG", action="drag", label=label,
              **{"from": [int(x1), int(y1)], "to": [int(x2), int(y2)]},
              duration_s=self.timing["drag_duration_s"], dry_run=self.dry_run)
        if not self.dry_run:
            pg = self._require_pg()
            steps = max(4, int(self.timing["drag_steps"]))
            delay = float(self.timing["drag_duration_s"]) / steps
            try:
                pg.moveTo(int(x1), int(y1))
                self.sleep(120)
                pg.mouseDown()
                for i in range(1, steps + 1):
                    self.check()
                    t = i / steps
                    pg.moveTo(int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t))
                    time.sleep(delay)
            except Exception as exc:
                if "FailSafe" in type(exc).__name__:
                    self.stop_event.set()
                    raise StopRequested() from exc
                raise
            finally:
                try:
                    pg.mouseUp()
                except Exception:
                    pass
        self.sleep(self.timing["after_drag_ms"])

    def scroll(self, x: int, y: int, clicks: int, label: str = "") -> None:
        """Wheel scroll at (x, y).  Positive = up, negative = down."""
        self.check()
        event("SCROLL", action="scroll", label=label, x=int(x), y=int(y),
              clicks=int(clicks), dry_run=self.dry_run)
        if not self.dry_run:
            pg = self._require_pg()
            try:
                pg.moveTo(int(x), int(y))
                self.sleep(60)
                pg.scroll(int(clicks))
            except Exception as exc:
                if "FailSafe" in type(exc).__name__:
                    self.stop_event.set()
                    raise StopRequested() from exc
                raise
        self.sleep(self.timing.get("after_scroll_ms", 350))

    def press(self, key: Optional[str], label: str = "key") -> None:
        if not key:
            return
        self.check()
        event("KEY", action="key", label=label, key=key, dry_run=self.dry_run)
        if not self.dry_run:
            try:
                self._require_pg().press(key)
            except Exception as exc:
                event("key press failed", level=30, error=str(exc))
        self.sleep(self.timing["after_click_ms"])

    def release(self) -> None:
        """Panic button: always let go of the mouse."""
        if self._pg is not None and not self.dry_run:
            try:
                self._pg.mouseUp()
            except Exception:
                pass
