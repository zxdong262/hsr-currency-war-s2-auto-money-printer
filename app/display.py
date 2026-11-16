"""Screen-resolution detection and switching for the two supported layouts.

Templates, ROIs and the bench geometry are all authored at 1440 px height;
``TemplateStore`` scales them to whatever frame height the capturer returns.
This module only handles the other half: telling the user which layout is
active and, after an explicit confirmation, changing the Windows display mode.
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Optional

from .logbus import LOG, event

IS_WINDOWS = os.name == "nt"

DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
ENUM_CURRENT_SETTINGS = -1
DISP_CHANGE_SUCCESSFUL = 0


class DEVMODEW(ctypes.Structure):
    """The Win32 DEVMODE layout up to dmDisplayFrequency (220 bytes on x64)."""
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmOrientation", ctypes.c_short),
        ("dmPaperSize", ctypes.c_short),
        ("dmPaperLength", ctypes.c_short),
        ("dmPaperWidth", ctypes.c_short),
        ("dmScale", ctypes.c_short),
        ("dmCopies", ctypes.c_short),
        ("dmDefaultSource", ctypes.c_short),
        ("dmPrintQuality", ctypes.c_short),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


def current() -> tuple[int, int]:
    """Primary display resolution, or (0, 0) when unavailable."""
    if not IS_WINDOWS:
        return (0, 0)
    try:
        u = ctypes.windll.user32
        return (int(u.GetSystemMetrics(0)), int(u.GetSystemMetrics(1)))
    except Exception as exc:
        LOG.debug("resolution probe failed: %s", exc)
        return (0, 0)


def profiles_from(cfg: dict) -> list[list[int]]:
    out: list[list[int]] = []
    for item in ((cfg.get("display") or {}).get("profiles")
                 or [[2560, 1440], [1920, 1080]]):
        try:
            w, h = int(item[0]), int(item[1])
        except (IndexError, TypeError, ValueError):
            continue
        if w >= 640 and h >= 480 and [w, h] not in out:
            out.append([w, h])
    return out or [[2560, 1440], [1920, 1080]]


def describe(cfg: dict, frame_size: Optional[tuple[int, int]] = None) -> dict:
    """Current resolution plus how far it is from a supported profile."""
    os_size = current()
    size = tuple(frame_size) if frame_size and frame_size[0] else os_size
    w, h = size
    ref = float((cfg.get("display") or {}).get("ref_height", 1440))
    profiles = profiles_from(cfg)
    active = None
    if h:
        active = min(profiles, key=lambda p: abs(p[1] - h) + abs(p[0] - w) * 0.1)
    scale = h / ref if h else 0.0
    supported = bool(active and abs(active[0] - w) <= 2 and abs(active[1] - h) <= 2)
    return {
        "current": [w, h],
        "os": list(os_size),
        "profiles": profiles,
        "active_profile": active,
        "scale": round(scale, 4),
        "supported": supported,
        "note": ("auto-scaled from the 1440px reference layout"
                 if supported else
                 "layout not in display.profiles - templates are still scaled, "
                 "but coordinates may need manual adjustment"),
    }


def set_resolution(width: int, height: int) -> tuple[bool, str]:
    """Switch the primary display mode. Does not need administrator rights.

    The caller (the web UI) must ask the user first - this function never
    changes the display silently. ChangeDisplaySettingsW with flags=0 is a
    dynamic change: it lasts for the session and is not written to the registry.
    """
    width, height = int(width), int(height)
    if not IS_WINDOWS:
        return False, "display switching is only available on Windows"
    if (width, height) == current():
        return True, f"already at {width}x{height}"
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    user32 = ctypes.windll.user32
    if not user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        return False, "could not read the current display mode"
    # Enum reports 188 (fields it filled); our struct has the full 220-byte
    # DEVMODE, which is what ChangeDisplaySettingsW expects here.
    dm.dmSize = ctypes.sizeof(DEVMODEW)
    dm.dmPelsWidth = width
    dm.dmPelsHeight = height
    dm.dmFields |= DM_PELSWIDTH | DM_PELSHEIGHT
    rc = int(user32.ChangeDisplaySettingsW(ctypes.byref(dm), 0))
    message = {
        0: "ok",
        1: "the display mode change needs a restart",
        -1: "the graphics driver rejected that mode",
        -2: "the mode is not supported by the driver",
        -3: "the mode failed after a test",
    }.get(rc, f"ChangeDisplaySettings returned {rc}")
    ok = rc == DISP_CHANGE_SUCCESSFUL
    event("DISPLAY MODE", action="display", level=20 if ok else 40,
          width=width, height=height, result=message, ok=ok)
    return ok, message
