"""Source-tree vs packaged-app paths and administrator re-launch.

When frozen by PyInstaller, templates and the pristine default config live inside
the bundle; the editable config, logs and screenshots live under %APPDATA%.
Running straight from the source tree keeps the old behaviour (everything in the
project directory), so development and tests are unaffected.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> str:
    """Where config.json and src/pngs are shipped."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def icon_path() -> str:
    """Bundled/ source-tree path of the Windows app icon."""
    return os.path.join(resource_dir(), "assets", "app-icon.ico")


def user_data_dir() -> str:
    """Writable per-user directory for settings, logs and debug screenshots."""
    if is_frozen():
        base = os.environ.get("APPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming")
        path = os.path.join(base, "AutoMoneyPrinter")
    else:
        path = resource_dir()
    os.makedirs(path, exist_ok=True)
    return path


@dataclass
class RuntimePaths:
    config_path: str
    root: str
    overrides: dict


def resolve_paths(config_path: Optional[str] = None) -> RuntimePaths:
    bundled = resource_dir()
    data = user_data_dir()
    overrides: dict = {}

    if config_path:
        path = os.path.abspath(config_path)
        root = os.path.dirname(path)
    elif is_frozen():
        path = os.path.join(data, "config.json")
        if not os.path.isfile(path):
            shutil.copyfile(os.path.join(bundled, "config.json"), path)
        root = data
    else:
        path = os.path.join(bundled, "config.json")
        root = bundled

    if is_frozen():
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            raw = {}
        tpl = raw.get("templates_dir", "src/pngs")
        if not (os.path.isabs(tpl) and os.path.isdir(tpl)):
            overrides["templates_dir"] = os.path.join(bundled, "src", "pngs")
        for key, default in (("log_dir", "logs"), ("screenshot_dir", "screenshot")):
            if os.path.isabs(str(raw.get(key, ""))):
                overrides[key] = default

    return RuntimePaths(path, root, overrides)


def elevate(argv: list[str]) -> bool:
    """Re-launch with administrator rights; return True if a new process started."""
    if os.name != "nt" or "--no-elevate" in argv:
        return False
    from .window import is_elevated
    if is_elevated():
        return False

    args = [a for a in argv if a != "--no-elevate"]
    if is_frozen():
        exe = sys.executable
        params = subprocess.list2cmdline(args)
        cwd = os.path.dirname(exe)
    else:
        exe = sys.executable
        entry = os.path.join(resource_dir(), "main.py")
        params = subprocess.list2cmdline([entry] + args)
        cwd = resource_dir()
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, cwd, 1)
    except Exception as exc:
        print(f"could not request administrator rights: {exc}", flush=True)
        return False
    if rc <= 32:  # UAC refused
        print("administrator rights were refused - clicks into the elevated game "
              "will be ignored by Windows.", flush=True)
        return False
    print("restarting with administrator rights (UAC)...", flush=True)
    return True
