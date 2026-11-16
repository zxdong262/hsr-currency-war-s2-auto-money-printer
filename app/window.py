"""Win32 window helpers: find, focus and describe the game window.

The bot can only see pixels that are actually on screen.  When it is started
from the browser console, the console is the window on top and the game is
buried underneath - every template lookup then fails with a confusing "market
button not visible".  These helpers bring the game back to the foreground
before the first screenshot is taken.
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Optional

SW_RESTORE = 9
WM_HOTKEY = 0x0312
VK_MENU = 0x12

if hasattr(ctypes, "windll"):
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
else:  # pragma: no cover - only used on Windows
    _user32 = None
    _kernel32 = None


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def available() -> bool:
    return _user32 is not None


def list_windows() -> list[dict]:
    """Visible top-level windows with a title, largest first."""
    if not available():
        return []
    out: list[dict] = []

    def _cb(hwnd, _lparam):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if not title:
            return True
        r = RECT()
        _user32.GetWindowRect(hwnd, ctypes.byref(r))
        out.append({
            "hwnd": int(hwnd),
            "title": title,
            "rect": (r.left, r.top, r.right, r.bottom),
            "minimized": bool(_user32.IsIconic(hwnd)),
            "area": max(0, r.right - r.left) * max(0, r.bottom - r.top),
        })
        return True

    _user32.EnumWindows(WNDENUMPROC(_cb), 0)
    out.sort(key=lambda w: w["area"], reverse=True)
    return out


def find_window(substr: str) -> Optional[dict]:
    """Biggest visible window whose title contains `substr` (case-insensitive)."""
    if not substr:
        return None
    needle = substr.lower()
    for w in list_windows():
        if needle in w["title"].lower():
            return w
    return None


def foreground_title() -> str:
    if not available():
        return ""
    hwnd = _user32.GetForegroundWindow()
    n = _user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_pid(hwnd: int) -> int:
    if not available() or not hwnd:
        return 0
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def is_elevated(pid: Optional[int] = None) -> Optional[bool]:
    """Is that process running as administrator?  None when it cannot be read."""
    if not available():
        return None
    pid = int(pid or _kernel32.GetCurrentProcessId())
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TokenElevation = 20

    class TOKEN_ELEVATION(ctypes.Structure):
        _fields_ = [("TokenIsElevated", wintypes.DWORD)]

    handle = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    token = wintypes.HANDLE()
    try:
        if not ctypes.windll.advapi32.OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        info = TOKEN_ELEVATION()
        size = wintypes.DWORD()
        ok = ctypes.windll.advapi32.GetTokenInformation(
            token, TokenElevation, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(size))
        return bool(info.TokenIsElevated) if ok else None
    finally:
        if token:
            _kernel32.CloseHandle(token)
        _kernel32.CloseHandle(handle)


def window_elevated(substr: str) -> Optional[bool]:
    win = find_window(substr)
    return is_elevated(window_pid(win["hwnd"])) if win else None


def activate_window(substr: str) -> tuple[bool, str]:
    """Bring the window matching `substr` to the foreground.

    Windows refuses SetForegroundWindow from a background process, so this
    attaches to the current foreground thread and taps ALT once - the standard
    workaround.  Returns (focused, human readable reason).
    """
    if not available():
        return False, "only supported on Windows"
    win = find_window(substr)
    if win is None:
        return False, f"no visible window matching {substr!r}"
    hwnd = win["hwnd"]
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(0.1)

    if _user32.GetForegroundWindow() == hwnd:
        return True, "already in the foreground"

    fg = _user32.GetForegroundWindow()
    tid_fg = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_me = _kernel32.GetCurrentThreadId()
    attached = False
    if tid_fg and tid_fg != tid_me:
        attached = bool(_user32.AttachThreadInput(tid_me, tid_fg, True))
    try:
        # one ALT tap defeats the foreground lock in most cases
        _user32.keybd_event(VK_MENU, 0, 0, 0)
        _user32.keybd_event(VK_MENU, 0, 2, 0)
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
        _user32.SetFocus(hwnd)
    finally:
        if attached:
            _user32.AttachThreadInput(tid_me, tid_fg, False)

    time.sleep(0.15)
    if _user32.GetForegroundWindow() == hwnd:
        return True, "brought to the foreground"
    return False, "Windows refused the focus change (foreground lock)"
