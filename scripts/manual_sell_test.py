"""One-off check: click the 出售 button of the card panel that is already open.

Run elevated (the game runs as administrator, so a non-elevated process cannot
send it any input).  Saves logs/debug/selltest-*.png and prints what happened.
"""
from __future__ import annotations

import json
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import Config                                  # noqa: E402
from app.vision import create_screen, MoneyReader, TemplateStore  # noqa: E402
from app.window import activate_window, is_elevated             # noqa: E402

OUT = os.path.join("logs", "debug")


def main() -> None:
    cfg = Config("config.json")
    raw = cfg.get()
    store = TemplateStore(raw, cfg.root)
    scr = create_screen(raw)
    reader = MoneyReader(raw, store)

    report = {"elevated": is_elevated()}
    print(json.dumps(report))

    ok, why = activate_window(raw.get("window_title") or "崩坏：星穹铁道")
    report["activate"] = [ok, why]
    time.sleep(0.8)

    before = scr.grab()
    cv2.imwrite(os.path.join(OUT, "selltest-before.png"), before.image)
    money_before = reader.read(before).value
    sell = store.find(before, "sell")
    report["money_before"] = money_before
    report["sell"] = None if sell is None else [sell.score, sell.x, sell.y,
                                                list(sell.box)]

    if sell is None:
        report["result"] = "sell button not found - is the card panel open?"
        print(json.dumps(report, ensure_ascii=False))
        return

    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.moveTo(sell.x, sell.y)
    time.sleep(0.2)
    pyautogui.click()
    time.sleep(0.7)

    after = scr.grab()
    cv2.imwrite(os.path.join(OUT, "selltest-after.png"), after.image)
    report["money_after"] = reader.read(after).value
    report["pillow_after"] = store.find(after, "sell") is not None
    report["result"] = "clicked"
    print(json.dumps(report, ensure_ascii=False))
    with open(os.path.join(OUT, "selltest-report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
