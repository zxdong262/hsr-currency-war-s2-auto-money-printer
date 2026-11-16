# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller recipe for the distributable native GUI app.

Run:  build.bat
Out:  dist/AutoMoneyPrinter/  and  dist/AutoMoneyPrinter.zip
"""
import os

from PyInstaller.utils.hooks import collect_all

root = os.path.abspath(os.getcwd())

datas = [
    (os.path.join(root, "config.json"), "."),
    (os.path.join(root, "src", "pngs"), os.path.join("src", "pngs")),
    (os.path.join(root, "assets", "app-icon.ico"), "assets"),
]
binaries = []
hiddenimports = [
    "app.context", "app.gui", "app.runtime", "app.display", "app.i18n",
    "app.hotkeys", "app.vision", "app.bot", "app.actions", "app.window",
    "_tkinter", "tkinter.ttk", "tkinter.font", "tkinter.messagebox",
]
# PyInstaller has built-in hooks for cv2 / onnxruntime / PIL / mss / pynput.
# Only the OCR models and the WGC capture package need explicit collection;
# collect_all() on every package would inflate the bundle to ~260 MB.
for package in ("rapidocr_onnxruntime", "windows_capture"):
    d, b, h = collect_all(package)
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)

# The legacy browser console is not part of the distributed app.
excludes = ["Flask", "flask", "jinja2", "werkzeug", "itsdangerous",
            "pytest", "matplotlib", "scipy"]

a = Analysis(
    [os.path.join(root, "main.py")],
    pathex=[root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=None)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AutoMoneyPrinter",
    icon=os.path.join(root, "assets", "app-icon.ico"),
    debug=False,
    console=False,
    # Elevation is requested at runtime by app/runtime.py, which also supports
    # --no-elevate for diagnostics and lets a UAC refusal show a useful message.
    uac_admin=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="AutoMoneyPrinter",
)
