"""One-off calibration: drag the 完美投影仪 onto a character and record every step.

Run elevated (the game runs as administrator, so a non-elevated process cannot
send it any input).  Writes logs/debug/dragtest-*.png plus a small JSON report.
"""
from __future__ import annotations

import json
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config                      # noqa: E402
from app.vision import create_screen, Frame, TemplateStore  # noqa: E402
from app.window import activate_window, is_elevated        # noqa: E402

OUT = os.path.join("logs", "debug")
report = {"elevated": is_elevated(), "steps": []}


def snap(scr, tag):
    f = scr.grab()
    path = os.path.join(OUT, f"dragtest-{tag}.png")
    cv2.imwrite(path, f.image)
    return f.image, path


def region_diff(a, b, box):
    x0, y0, x1, y1 = box
    d = cv2.absdiff(a[y0:y1, x0:x1], b[y0:y1, x0:x1])
    return float((d.max(axis=2) > 28).mean())


def main() -> None:
    cfg = Config("config.json")
    raw = cfg.get()
    store = TemplateStore(raw, cfg.root)
    scr = create_screen(raw)
    import pyautogui

    pyautogui.FAILSAFE = False
    pg = pyautogui

    print("elevated:", is_elevated())
    print("activate:", activate_window(raw.get("window_title", "崩坏：星穹铁道")))
    time.sleep(1.0)

    base, _ = snap(scr, "00-base")
    f = Frame(base, (0, 0), "base")
    src = store.find(f, "drag_item")
    if not src:
        report["error"] = "drag_item not found"
        print(json.dumps(report, ensure_ascii=False))
        return
    report["source"] = [src.x, src.y, round(src.score, 3)]
    print("source", src.x, src.y, round(src.score, 3))

    inv_box = (2140, 150, 2560, 300)      # the top row of the item grid
    bench_box = (280, 1090, 1900, 1400)   # 备战席
    targets = eval(sys.argv[1]) if len(sys.argv) > 1 else [[1186, 885], [798, 520]]

    for idx, (tx, ty) in enumerate(targets):
        before, _ = snap(scr, f"{idx}-a-before")
        pg.moveTo(src.x, src.y)
        time.sleep(0.25)
        pg.mouseDown()
        time.sleep(0.30)
        down, _ = snap(scr, f"{idx}-b-down")

        steps = 18
        for i in range(1, steps + 1):
            pg.moveTo(int(src.x + (tx - src.x) * i / steps),
                      int(src.y + (ty - src.y) * i / steps))
            time.sleep(0.025)
        time.sleep(0.45)
        over, _ = snap(scr, f"{idx}-c-over")

        pg.mouseUp()
        time.sleep(0.55)
        after, _ = snap(scr, f"{idx}-d-after")
        time.sleep(0.9)
        after2, _ = snap(scr, f"{idx}-e-after2")

        step = {
            "target": [tx, ty],
            "inventory_changed": round(region_diff(before, after2, inv_box), 4),
            "bench_changed": round(region_diff(before, after2, bench_box), 4),
            "drag_visible_at_down": round(region_diff(before, down, (2140, 150, 2560, 300)), 4),
            "banner_changed_at_over": round(region_diff(before, over, (900, 300, 1800, 420)), 4),
        }
        report["steps"].append(step)
        print(json.dumps(step, ensure_ascii=False))

    with open(os.path.join(OUT, "dragtest-report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print("done")


if __name__ == "__main__":
    main()
