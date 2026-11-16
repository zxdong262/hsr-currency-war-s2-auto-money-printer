"""Screen capture + OpenCV template matching + money OCR."""
from __future__ import annotations

import base64
import ctypes
import os
import re
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Optional

import cv2
import numpy as np

from .logbus import LOG

# ---------------------------------------------------------------- DPI / io --

def enable_dpi_awareness() -> None:
    """Make screenshot pixels map 1:1 to physical mouse coordinates on Windows."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def imread_unicode(path: str, flags: int = cv2.IMREAD_UNCHANGED) -> Optional[np.ndarray]:
    """cv2.imread cannot open non-ASCII Windows paths; decode from bytes instead."""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        if buf.size == 0:
            return None
        return cv2.imdecode(buf, flags)
    except Exception:
        return None


def png_data_url(bgr: np.ndarray, max_width: int = 1600) -> str:
    if bgr is None or bgr.size == 0:
        return ""
    h, w = bgr.shape[:2]
    if w > max_width:
        s = max_width / w
        bgr = cv2.resize(bgr, (max_width, max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        return ""
    return "data:image/png;base64," + base64.b64encode(buf.tobytes()).decode("ascii")


@dataclass
class Frame:
    image: np.ndarray
    origin: tuple[int, int] = (0, 0)
    source: str = "live"

    @property
    def size(self) -> tuple[int, int]:
        return self.image.shape[1], self.image.shape[0]


class ScreenSource:
    def grab(self) -> Frame:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:
        pass


class MssScreen(ScreenSource):
    """0 = whole virtual desktop, 1..n = individual monitors."""

    def __init__(self, monitor: int = 0, region: Optional[list] = None):
        import mss
        self._sct = mss.mss()
        self._monitor = int(monitor)
        self._region = region
        self._lock = threading.Lock()

    def _rect(self) -> dict:
        if self._region:
            l, t, w, h = [int(v) for v in self._region]
            return {"left": l, "top": t, "width": w, "height": h}
        mons = self._sct.monitors
        idx = self._monitor if 0 <= self._monitor < len(mons) else 0
        return mons[idx]

    def grab(self) -> Frame:
        rect = self._rect()
        with self._lock:
            raw = self._sct.grab(rect)
            arr = np.asarray(raw, dtype=np.uint8).copy()
        bgr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
        return Frame(bgr, (rect["left"], rect["top"]), "live")

    def monitors(self) -> list[dict]:
        return [dict(m) for m in self._sct.monitors]

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass


class ImageScreen(ScreenSource):
    """Offline replay: a saved screenshot acts as the 'screen' (origin 0,0)."""

    def __init__(self, path: str):
        img = imread_unicode(path, cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"cannot read screenshot: {path}")
        self.image = img
        self.source = os.path.basename(path)

    def grab(self) -> Frame:
        return Frame(self.image.copy(), (0, 0), self.source)


class WgcScreen(ScreenSource):
    """Windows Graphics Capture - the only backend that sees a DX fullscreen game.

    ``mss`` uses a GDI BitBlt of the desktop surface.  A flip-model / fullscreen
    game presents straight to the display and that surface keeps the *previous*
    desktop content, so template matching silently fails even though the game is
    right there on screen.  WGC asks DWM for the window's actual frames.
    """

    def __init__(self, window_name: Optional[str] = None,
                 monitor_index: Optional[int] = None,
                 timeout_s: float = 4.0, origin: Optional[tuple[int, int]] = None):
        if os.name != "nt":
            raise RuntimeError("WGC capture is Windows only")
        self.window_name = window_name or None
        self.monitor_index = int(monitor_index) if monitor_index else None
        self.timeout = float(timeout_s)
        self._origin = origin or (0, 0)
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._seq = 0
        self._closed = False
        self._control = None
        self._cap = None
        self._start()

    # -- capture session -----------------------------------------------------

    def _start(self) -> None:
        from windows_capture import WindowsCapture

        kwargs: dict = {"cursor_capture": False, "draw_border": False}
        origin = (0, 0)
        if self.window_name:
            from .window import find_window
            win = find_window(self.window_name)
            if win is None:
                raise RuntimeError(f"game window {self.window_name!r} is not running")
            kwargs["window_hwnd"] = win["hwnd"]
            origin = (win["rect"][0], win["rect"][1])
        elif self.monitor_index:
            kwargs["monitor_index"] = self.monitor_index
        else:
            kwargs["monitor_index"] = 1

        cap = WindowsCapture(**kwargs)

        @cap.event
        def on_frame_arrived(frame, control):
            buf = frame.frame_buffer
            img = np.ascontiguousarray(buf[:, :, :3])   # BGRA -> BGR
            with self._lock:
                self._frame = img
                self._seq += 1

        @cap.event
        def on_closed():
            self._closed = True

        self._cap = cap
        self._origin = origin
        self._closed = False
        self._control = cap.start_free_threaded()

    def _restart(self) -> None:
        self.close()
        self._start()

    # -- ScreenSource --------------------------------------------------------

    def grab(self) -> Frame:
        if self._closed or self._frame is None:
            if self._closed:
                self._restart()
            else:
                deadline = time.time() + self.timeout
                while time.time() < deadline:
                    if self._closed:
                        self._restart()
                        break
                    with self._lock:
                        if self._frame is not None:
                            break
                    time.sleep(0.01)
        with self._lock:
            if self._frame is None:
                raise RuntimeError("WGC capture produced no frames "
                                   "(is the game window still alive?)")
            img = self._frame.copy()
            origin = self._origin
        return Frame(img, origin, "wgc")

    def monitors(self) -> list[dict]:
        try:
            import mss
            with mss.mss() as sct:
                return [dict(m) for m in sct.monitors]
        except Exception:
            return []

    def close(self) -> None:
        control, self._control = self._control, None
        if control is not None:
            try:
                control.stop()
            except Exception:
                pass
        self._cap = None
        self._closed = True


def list_monitors() -> list[dict]:
    try:
        import mss
        with mss.mss() as sct:
            return [dict(m) for m in sct.monitors]
    except Exception:
        return []


def create_screen(cfg: dict) -> ScreenSource:
    """Pick the capture backend.

    ``capture.backend``: ``auto`` (WGC when available) | ``wgc`` | ``mss``.
    ``capture.window``: window title substring; window capture keeps working
    while the console is on top.  Without it, the monitor is captured instead.
    """
    cap = cfg.get("capture") or {}
    backend = str(cap.get("backend", "auto")).lower()
    if backend in ("auto", "wgc"):
        try:
            return WgcScreen(
                window_name=cap.get("window"),
                monitor_index=cap.get("monitor_index") or (int(cfg.get("monitor", 0)) or 1),
                timeout_s=float(cap.get("timeout_s", 4.0)))
        except Exception as exc:
            LOG.warning("WGC capture unavailable (%s); falling back to mss", exc)
            if backend == "wgc":
                raise
    return MssScreen(int(cfg.get("monitor", 0)), cfg.get("region"))


# ------------------------------------------------------------- template match --

@dataclass
class Match:
    name: str
    score: float
    scale: float
    x: int                     # click anchor, screen coords
    y: int
    left: int                  # full template origin, screen coords
    top: int
    width: int
    height: int

    @property
    def box(self) -> list[int]:
        return [self.left, self.top, self.left + self.width, self.top + self.height]

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "score": round(float(self.score), 4),
            "scale": self.scale,
            "x": self.x,
            "y": self.y,
            "box": self.box,
        }


@dataclass
class Template:
    name: str
    image: np.ndarray          # possibly cropped for matching
    mask: Optional[np.ndarray]
    full_size: tuple[int, int]  # (w, h) of the uncropped template
    crop: tuple[int, int]       # (cx, cy) crop origin inside full template
    anchor: tuple[float, float]
    threshold: float
    roi: Optional[list]
    scales: list[float] = field(default_factory=lambda: [1.0])

    @property
    def filename(self) -> str:
        return f"{self.name}.png"


class TemplateStore:
    """Loads every template once and searches the current frame for them."""

    def __init__(self, cfg: dict, root: str):
        self.cfg = cfg
        self.root = root
        self.tpl_dir = os.path.join(root, cfg["templates_dir"]) if not os.path.isabs(cfg["templates_dir"]) else cfg["templates_dir"]
        self.default_threshold = float(cfg["match"]["threshold"])
        self.default_scales = [float(s) for s in cfg["match"].get("scales", [1.0])] or [1.0]
        self.nms_iou = float(cfg["match"].get("nms_iou", 0.35))
        self.use_roi = bool(cfg["match"].get("use_roi", True))
        # Every ROI/crop/anchor in config.json is expressed at this height.
        # Frames at other resolutions (1080p / 1440p) are matched with a scaled
        # copy of each template instead of keeping a second config per display.
        self.ref_height = float((cfg.get("display") or {}).get("ref_height", 1440))
        self.templates: dict[str, Template] = {}
        self.reload()

    def frame_scale(self, frame: Frame) -> float:
        """Template/ROI scale for the current frame height."""
        return frame.image.shape[0] / self.ref_height

    def _view(self, tpl: Template, scale: float) -> Template:
        """A template scaled to the current screen, with all coordinates adjusted."""
        if abs(scale - 1.0) < 1e-3:
            return tpl
        interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        image = cv2.resize(tpl.image, None, fx=scale, fy=scale, interpolation=interp)
        kw = {
            "image": image,
            "full_size": (int(round(tpl.full_size[0] * scale)),
                          int(round(tpl.full_size[1] * scale))),
            "crop": (int(round(tpl.crop[0] * scale)), int(round(tpl.crop[1] * scale))),
            "anchor": (tpl.anchor[0] * scale, tpl.anchor[1] * scale),
            "roi": ([int(round(v * scale)) for v in tpl.roi] if tpl.roi else None),
        }
        if tpl.mask is not None:
            kw["mask"] = cv2.resize(tpl.mask, (image.shape[1], image.shape[0]),
                                    interpolation=cv2.INTER_NEAREST)
        return replace(tpl, **kw)

    def _roi(self, tpl: Template) -> Optional[list]:
        """ROIs are tied to one screen layout; the UI can disable them all at once."""
        return tpl.roi if self.use_roi else None

    def reload(self) -> None:
        self.templates.clear()
        for name, spec in self.cfg["templates"].items():
            path = os.path.join(self.tpl_dir, spec["file"])
            img = imread_unicode(path)
            if img is None:
                raise FileNotFoundError(f"template not readable: {path}")
            mask = None
            if img.ndim == 3 and img.shape[2] == 4:
                alpha = img[:, :, 3]
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                if np.any(alpha < 250):
                    mask = np.repeat(alpha[:, :, None], 3, axis=2)
            elif img.ndim == 2:
                img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
            full_h, full_w = img.shape[:2]
            cx, cy, cw, ch = spec.get("crop") or [0, 0, full_w, full_h]
            cx, cy, cw, ch = int(cx), int(cy), int(cw), int(ch)
            cw = min(cw, full_w - cx)
            ch = min(ch, full_h - cy)
            sub = img[cy:cy + ch, cx:cx + cw]
            submask = mask[cy:cy + ch, cx:cx + cw] if mask is not None else None
            anchor = spec.get("anchor") or [full_w / 2, full_h / 2]
            self.templates[name] = Template(
                name=name,
                image=sub,
                mask=submask,
                full_size=(full_w, full_h),
                crop=(cx, cy),
                anchor=(float(anchor[0]), float(anchor[1])),
                threshold=float(spec.get("threshold", self.default_threshold)),
                roi=spec.get("roi"),
                scales=[float(s) for s in spec.get("scales", self.default_scales)] or [1.0],
            )

    def _sub(self, frame: Frame, roi: Optional[list]) -> tuple[np.ndarray, int, int]:
        h, w = frame.image.shape[:2]
        if roi:
            l, t, r, b = [int(v) for v in roi]
            x0 = max(0, l - frame.origin[0])
            y0 = max(0, t - frame.origin[1])
            x1 = min(w, r - frame.origin[0])
            y1 = min(h, b - frame.origin[1])
        else:
            x0, y0, x1, y1 = 0, 0, w, h
        if x1 <= x0 or y1 <= y0:
            return frame.image[0:0, 0:0], x0, y0
        return frame.image[y0:y1, x0:x1], x0, y0

    @staticmethod
    def _method(tpl: Template) -> int:
        return cv2.TM_CCORR_NORMED if tpl.mask is not None else cv2.TM_CCOEFF_NORMED

    def _make_match(self, tpl: Template, frame: Frame, mx: int, my: int, score: float,
                    scale: float, sub_x: int, sub_y: int) -> Match:
        # crop -> full template origin -> anchor, all expressed in screen coordinates
        left = frame.origin[0] + sub_x + mx - tpl.crop[0] * scale
        top = frame.origin[1] + sub_y + my - tpl.crop[1] * scale
        x = left + tpl.anchor[0] * scale
        y = top + tpl.anchor[1] * scale
        return Match(
            name=tpl.name, score=float(score), scale=scale,
            x=int(round(x)), y=int(round(y)),
            left=int(round(left)), top=int(round(top)),
            width=int(round(tpl.full_size[0] * scale)),
            height=int(round(tpl.full_size[1] * scale)),
        )

    def find(self, frame: Frame, name: str, threshold: Optional[float] = None) -> Optional[Match]:
        tpl = self.templates.get(name)
        if tpl is None:
            raise KeyError(f"unknown template: {name}")
        tpl = self._view(tpl, self.frame_scale(frame))
        sub, sx, sy = self._sub(frame, self._roi(tpl))
        if sub.size == 0:
            return None
        need = threshold if threshold is not None else tpl.threshold
        best: Optional[Match] = None
        method = self._method(tpl)
        for scale in tpl.scales:
            img = tpl.image if scale == 1.0 else cv2.resize(
                tpl.image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
            mask = None
            if tpl.mask is not None:
                mask = tpl.mask if scale == 1.0 else cv2.resize(
                    tpl.mask, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_NEAREST)
            if img.shape[0] > sub.shape[0] or img.shape[1] > sub.shape[1]:
                continue
            try:
                res = cv2.matchTemplate(sub, img, method, mask=mask) if mask is not None \
                    else cv2.matchTemplate(sub, img, method)
            except cv2.error as exc:
                LOG.warning("matchTemplate failed for %s: %s", name, exc)
                continue
            _, mxv, _, mloc = cv2.minMaxLoc(res)
            if best is None or mxv > best.score:
                best = self._make_match(tpl, frame, mloc[0], mloc[1], mxv, scale, sx, sy)
        if best is None or best.score < need:
            return None
        return best

    def find_all(self, frame: Frame, name: str, threshold: Optional[float] = None,
                 limit: int = 8) -> list[Match]:
        tpl = self.templates.get(name)
        if tpl is None:
            return []
        tpl = self._view(tpl, self.frame_scale(frame))
        sub, sx, sy = self._sub(frame, self._roi(tpl))
        if sub.size == 0 or tpl.image.shape[0] > sub.shape[0] or tpl.image.shape[1] > sub.shape[1]:
            return []
        need = threshold if threshold is not None else tpl.threshold
        method = self._method(tpl)
        try:
            res = cv2.matchTemplate(sub, tpl.image, method, mask=tpl.mask) if tpl.mask is not None \
                else cv2.matchTemplate(sub, tpl.image, method)
        except cv2.error:
            return []
        ys, xs = np.where(res >= need)
        if len(xs) == 0:
            return []
        scores = res[ys, xs]
        order = np.argsort(-scores)
        picked: list[Match] = []
        for i in order:
            m = self._make_match(tpl, frame, int(xs[i]), int(ys[i]), float(scores[i]), 1.0, sx, sy)
            if any(_iou(m.box, p.box) > self.nms_iou for p in picked):
                continue
            picked.append(m)
            if len(picked) >= limit:
                break
        return picked

    def score(self, frame: Frame, name: str) -> float:
        """Best raw score for one template, without the cost of probing all of them."""
        tpl = self.templates.get(name)
        if tpl is None:
            return 0.0
        tpl = self._view(tpl, self.frame_scale(frame))
        sub, _, _ = self._sub(frame, self._roi(tpl))
        if sub.size == 0 or tpl.image.shape[0] > sub.shape[0] or tpl.image.shape[1] > sub.shape[1]:
            return 0.0
        try:
            res = cv2.matchTemplate(sub, tpl.image, self._method(tpl), mask=tpl.mask) \
                if tpl.mask is not None else cv2.matchTemplate(sub, tpl.image, self._method(tpl))
            _, mxv, _, _ = cv2.minMaxLoc(res)
            return float(mxv)
        except cv2.error:
            return 0.0

    def probe(self, frame: Frame, threshold: Optional[float] = None) -> tuple[list[Match], dict[str, float]]:
        """Find every template; returns matches and the best raw score of each."""
        found: list[Match] = []
        scores: dict[str, float] = {}
        ui_scale = self.frame_scale(frame)
        for name, tpl in self.templates.items():
            tpl = self._view(tpl, ui_scale)
            sub, sx, sy = self._sub(frame, self._roi(tpl))
            if sub.size == 0 or tpl.image.shape[0] > sub.shape[0] or tpl.image.shape[1] > sub.shape[1]:
                scores[name] = 0.0
                continue
            method = self._method(tpl)
            try:
                res = cv2.matchTemplate(sub, tpl.image, method, mask=tpl.mask) if tpl.mask is not None \
                    else cv2.matchTemplate(sub, tpl.image, method)
                _, mxv, _, mloc = cv2.minMaxLoc(res)
            except cv2.error:
                scores[name] = 0.0
                continue
            scores[name] = round(float(mxv), 4)
            need = threshold if threshold is not None else tpl.threshold
            if mxv >= need:
                found.append(self._make_match(tpl, frame, mloc[0], mloc[1], mxv, 1.0, sx, sy))
        return found, scores

    def annotate(self, frame: Frame, matches: list[Match]) -> np.ndarray:
        img = frame.image.copy()
        palette = [
            (0, 200, 0), (0, 140, 255), (220, 40, 40), (200, 0, 200),
            (0, 200, 220), (230, 150, 0), (120, 220, 0), (255, 100, 180),
        ]
        for i, m in enumerate(matches):
            c = palette[i % len(palette)]
            x0, y0 = m.left - frame.origin[0], m.top - frame.origin[1]
            x1, y1 = x0 + m.width, y0 + m.height
            cv2.rectangle(img, (x0, y0), (x1, y1), c, 2)
            ax, ay = m.x - frame.origin[0], m.y - frame.origin[1]
            cv2.drawMarker(img, (ax, ay), c, cv2.MARKER_CROSS, 14, 2)
            label = f"{m.name} {m.score:.2f}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
            cv2.rectangle(img, (x0, max(0, y0 - th - 8)), (x0 + tw + 8, y0), c, -1)
            cv2.putText(img, label, (x0 + 4, max(th + 2, y0 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        return img


def _iou(a: list[int], b: list[int]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


# -------------------------------------------------------------------- text OCR --

_OCR_ENGINE = None
_OCR_FAILED = False
_OCR_LOCK = threading.Lock()


def get_ocr_engine():
    """One shared RapidOCR instance for the whole app.

    Loading the models costs about a second and the inference session is not
    thread safe, so both the money reader and the label locator use this single
    engine and serialise every call through ``_OCR_LOCK``.
    """
    global _OCR_ENGINE, _OCR_FAILED
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE
    if _OCR_FAILED:
        return None
    try:
        from rapidocr_onnxruntime import RapidOCR
        _OCR_ENGINE = RapidOCR()
        LOG.info("OCR engine ready: rapidocr (bundled, offline)")
    except Exception as exc:
        _OCR_FAILED = True
        LOG.warning("rapidocr unavailable: %s", exc)
    return _OCR_ENGINE


@dataclass
class TextHit:
    """One recognised text line, in screen coordinates."""
    text: str
    score: float
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> tuple[int, int]:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2

    def as_dict(self) -> dict:
        return {"text": self.text, "score": round(float(self.score), 3),
                "box": [self.left, self.top, self.right, self.bottom]}


class TextLocator:
    """Finds a UI label by OCR so the button next to it can be clicked.

    Template matching on an icon that lives in a scrolling list is fragile: the
    backdrop behind the icon changes with every scroll, and the match score
    collapses even though the icon did not move.  The label text next to it is
    stable, so read the label and derive the icon position from its box.
    """

    def __init__(self, spec: Optional[dict] = None):
        self.spec = spec or {}
        self.min_score = float(self.spec.get("min_score", 0.5))

    def find_all(self, frame: Frame, keywords, roi=None) -> list[TextHit]:
        engine = get_ocr_engine()
        if engine is None:
            return []
        keys = [str(k) for k in (keywords or []) if k]
        if not keys:
            return []
        h, w = frame.image.shape[:2]
        if roi:
            l, t, r, b = [int(v) for v in roi]
            x0 = max(0, l - frame.origin[0])
            y0 = max(0, t - frame.origin[1])
            x1 = min(w, r - frame.origin[0])
            y1 = min(h, b - frame.origin[1])
        else:
            x0, y0, x1, y1 = 0, 0, w, h
        if x1 - x0 < 24 or y1 - y0 < 24:
            return []
        crop = frame.image[y0:y1, x0:x1]
        with _OCR_LOCK:
            try:
                out, _ = engine(crop)
            except Exception as exc:
                LOG.warning("text OCR failed: %s", exc)
                return []
        hits: list[TextHit] = []
        for item in (out or []):
            try:
                box, text, score = item[0], str(item[1]), float(item[2])
            except (IndexError, TypeError, ValueError):
                continue
            if score < self.min_score or not any(k in text for k in keys):
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
            hits.append(TextHit(
                text=text, score=score,
                left=int(min(xs)) + x0 + frame.origin[0],
                top=int(min(ys)) + y0 + frame.origin[1],
                right=int(max(xs)) + x0 + frame.origin[0],
                bottom=int(max(ys)) + y0 + frame.origin[1]))
        hits.sort(key=lambda hit: -hit.score)
        return hits

    def find(self, frame: Frame, keywords, roi=None) -> Optional[TextHit]:
        hits = self.find_all(frame, keywords, roi)
        return hits[0] if hits else None


# ------------------------------------------------------------------- money OCR --

@dataclass
class MoneyResult:
    value: Optional[int]
    raw: str
    backend: str
    crop: Optional[np.ndarray] = None
    box: Optional[list[int]] = None


class MoneyReader:
    """Reads the number to the right of the coin icon in money-count.png.

    The coin itself is the stable anchor; the digits change, so they are OCR'd
    from a relative ROI instead of being matched as a template.

    The shop overlay draws the coin a little differently (it matches at ~0.6,
    below any usable threshold), so if the anchor is missing the digits are
    OCR'd straight from a fixed corner ROI - guarded by a brightness check so a
    card-info popup covering that corner is never mistaken for the counter.
    """

    def __init__(self, cfg: dict, store: TemplateStore):
        self.cfg = cfg["money"]
        self.store = store
        self._ocr = None
        self._backend = None
        self._lock = threading.Lock()
        self._warned = False

    def crop_digits(self, frame: Frame, anchor: Optional[Match] = None) -> tuple[Optional[np.ndarray], Optional[list[int]]]:
        if anchor is None:
            anchor = self.store.find(frame, self.cfg["anchor_template"], self.cfg.get("threshold"))
        if anchor is None:
            return None, None
        s = self.store.frame_scale(frame)
        dx, dy, dw, dh = [int(v) for v in self.cfg["digit_roi"]]
        x0 = anchor.left - frame.origin[0] + int(round(dx * s))
        y0 = anchor.top - frame.origin[1] + int(round(dy * s))
        h, w = frame.image.shape[:2]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x0 + int(round(dw * s))), min(h, y0 + int(round(dh * s)))
        if x1 <= x0 or y1 <= y0:
            return None, None
        crop = frame.image[y0:y1, x0:x1].copy()
        box = [x0 + frame.origin[0], y0 + frame.origin[1], x1 + frame.origin[0], y1 + frame.origin[1]]
        return crop, box

    def _prep(self, crop: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        gray = cv2.copyMakeBorder(gray, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=245)
        return gray

    def _prep_binary(self, crop: np.ndarray) -> np.ndarray:
        """Binarised copy of the digits.

        PP-OCR reads this font's 6 as 9 (and vice versa) on the light money
        card: the anti-aliased gap of a 6 looks like the bowl of a 9.  With a
        hard Otsu threshold the glyph shape survives, and the same engine then
        gets it right - so the binary pass is the tie-breaker for one-digit
        counters, which is exactly when the reading decides "buy or stop".
        """
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return cv2.copyMakeBorder(th, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)

    def _init_ocr(self) -> str:
        if self._backend:
            return self._backend
        mode = self.cfg.get("ocr", "auto")
        if mode in ("auto", "rapidocr"):
            engine = get_ocr_engine()
            if engine is not None:
                self._ocr = engine
                self._backend = "rapidocr"
                LOG.info("money OCR backend: rapidocr (bundled, offline)")
                return self._backend
        if mode in ("auto", "tesseract"):
            try:
                import pytesseract
                cmd = self.cfg.get("tesseract_cmd")
                if cmd:
                    pytesseract.pytesseract.tesseract_cmd = cmd
                pytesseract.get_tesseract_version()
                self._ocr = pytesseract
                self._backend = "tesseract"
                LOG.info("money OCR backend: tesseract")
                return self._backend
            except Exception as exc:
                LOG.debug("tesseract unavailable: %s", exc)
        self._backend = "ledger"
        if not self._warned:
            LOG.warning("No OCR engine found (install rapidocr-onnxruntime or Tesseract). "
                        "Money will be tracked by the arithmetic ledger; set "
                        "economy.starting_money or use the Money field in the UI.")
            self._warned = True
        return self._backend

    @staticmethod
    def _digits(text: str) -> Optional[int]:
        nums = re.findall(r"\d+", text or "")
        if not nums:
            return None
        return int(max(nums, key=len))

    # ----------------------------------------------------- fixed ROI fallback --

    def fallback_crop(self, frame: Frame) -> tuple[Optional[np.ndarray], Optional[list[int]]]:
        """Digits-only crop of the counter, found without the coin template."""
        h, w = frame.image.shape[:2]
        rel = self.cfg.get("fallback_roi_rel")
        if rel:
            x0, y0, x1, y1 = (int(float(rel[0]) * w), int(float(rel[1]) * h),
                              int(float(rel[2]) * w), int(float(rel[3]) * h))
        else:
            abs_roi = self.cfg.get("fallback_roi")
            if not abs_roi:
                return None, None
            x0, y0, x1, y1 = [int(v) for v in abs_roi]
            x0 -= frame.origin[0]
            x1 -= frame.origin[0]
            y0 -= frame.origin[1]
            y1 -= frame.origin[1]
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(w, x1), min(h, y1)
        if x1 - x0 < 20 or y1 - y0 < 15:
            return None, None
        box = [x0 + frame.origin[0], y0 + frame.origin[1],
               x1 + frame.origin[0], y1 + frame.origin[1]]
        return frame.image[y0:y1, x0:x1].copy(), box

    @staticmethod
    def _panel_like(crop: np.ndarray) -> bool:
        """True when the crop still looks like the light money card.

        A card-info popup over that corner is much darker, so this keeps the
        fallback from inventing a number out of unrelated pixels.
        """
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        dark = float((gray < 90).mean())
        return float(gray.mean()) >= 70.0 and dark <= 0.85

    def _recognize(self, backend: str, img: np.ndarray) -> str:
        if backend == "rapidocr":
            with _OCR_LOCK:          # one shared engine, not thread safe
                out, _ = self._ocr(img)
            return " ".join(str(item[1]) for item in out) if out else ""
        return self._ocr.image_to_string(
            img, config="--psm 7 -c tessedit_char_whitelist=0123456789").strip()

    def _ocr_crop(self, crop, box, suffix: str = "") -> MoneyResult:
        with self._lock:
            backend = self._init_ocr()
            raw = ""
            try:
                raw = self._recognize(backend, self._prep(crop))
                value = self._digits(raw)
                if backend == "rapidocr" and (value is None or value < 10):
                    raw2 = self._recognize(backend, self._prep_binary(crop))
                    value2 = self._digits(raw2)
                    if value2 is not None and value2 != value:
                        LOG.info("money OCR differs on the last digit: raw=%r binary=%r "
                                 "-> taking the binarised read %d", raw, raw2, value2)
                        raw, value = raw2, value2
                        suffix = f"{suffix}-bin" if suffix else "bin"
            except Exception as exc:
                LOG.warning("OCR failed: %s", exc)
                return MoneyResult(None, raw, backend + "-error", crop, box)
        name = backend + (f"-{suffix}" if suffix else "")
        return MoneyResult(self._digits(raw), raw, name, crop, box)

    def read(self, frame: Frame, anchor: Optional[Match] = None) -> MoneyResult:
        crop, box = self.crop_digits(frame, anchor)
        result = None
        if crop is not None:
            result = self._ocr_crop(crop, box)
            if result.value is not None:
                return result

        fb_crop, fb_box = self.fallback_crop(frame)
        if fb_crop is not None and self._panel_like(fb_crop):
            fb = self._ocr_crop(fb_crop, fb_box, "fixed-roi")
            if fb.value is not None:
                if result is None:
                    LOG.debug("money read from the fixed corner ROI (anchor missing)")
                else:
                    LOG.debug("money read from the fixed corner ROI (anchor OCR gave %r)", result.raw)
                return fb
            if result is None:
                return fb
        if result is not None:
            return result
        return MoneyResult(None, "", "anchor-not-found")
