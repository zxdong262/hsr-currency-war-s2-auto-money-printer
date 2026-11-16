"""Offline regression test: no mouse input, no game window needed.

Run with:  .venv/Scripts/python.exe tests/test_offline.py

It replays the four supplied screenshots as a scripted "screen" so the whole
state machine (buy loop -> duplicate -> sell) can be exercised safely.
"""
from __future__ import annotations

import copy
import os
import sys
import threading

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.actions import Actions                      # noqa: E402
from app.bot import Bot, State                      # noqa: E402
from app.config import Config                       # noqa: E402
from app.logbus import setup_logging                 # noqa: E402
from app.vision import Frame, ImageScreen, MoneyReader, TemplateStore  # noqa: E402

SHOTS = os.path.join(ROOT, "screenshot")


def shot(n: int) -> Frame:
    import glob
    return ImageScreen(sorted(glob.glob(os.path.join(SHOTS, "*.png")))[n]).grab()


def mask_cards(frame: Frame, boxes: list[list[int]]) -> Frame:
    img = frame.image.copy()
    for x0, y0, x1, y1 in boxes:
        cv2.rectangle(img, (x0, y0), (x1, y1), (34, 20, 48), -1)
    return Frame(img, frame.origin, "simulated")


class Scenario:
    def __init__(self):
        s1, s2, s3, s4 = (shot(i) for i in range(4))
        self.frames = {
            "closed": s1,
            "market": s2,                       # shop open, 完美投影仪 available
            "cards": s3,                        # two duplicated cards
            "cards1": mask_cards(s3, [[670, 1110, 850, 1320]]),   # only the first copy
            "info": s4,                         # card info + 出售 button
            "cards2": mask_cards(s3, [[500, 1110, 670, 1320]]),   # one card sold
            "empty": mask_cards(s3, [[500, 1110, 850, 1320]]),    # both sold
        }
        # an item grid without the projector icon: the bot then has to go buy one
        # (with stock left it deliberately skips the shop)
        self.frames["closed_no_item"] = mask_cards(s1, [[2395, 155, 2535, 295]])
        self.stage = "closed"
        self.drags = 0

    def grab(self) -> Frame:
        key = {"closed": "closed_no_item", "closed_full": "closed"}.get(
            self.stage, self.stage)
        f = self.frames[key]
        return Frame(f.image.copy(), f.origin, self.stage)

    def on_action(self, label: str) -> None:
        if label.startswith("open market"):
            self.stage = "market"
        elif label.startswith("duplicate"):
            self.drags += 1
            # one drag = one more copy in the strip
            if self.drags == 1:
                self.stage = "cards1"
            elif self.drags == 2:
                self.stage = "cards"
        elif label.startswith("open card info"):
            self.stage = "info"
        elif label.startswith("sell card"):
            self.stage = "empty" if self.stage == "cards2" else "cards2"


class FlickerScenario(Scenario):
    """The shop opens on mouse-down and the mouse-up hits the backdrop: the
    panel is gone again by the time we look.  Only releasing inside the panel
    ('into_panel') survives."""

    def on_action(self, label: str) -> None:
        if label.startswith("open market"):
            self.stage = "market" if label.endswith("into_panel") else "closed"
            return
        super().on_action(label)


class SimActions(Actions):
    """No real input, no real waiting, but the same logging/ledger path."""

    def __init__(self, cfg, stop, scenario):
        super().__init__(cfg, stop, dry_run=True, log_dir=os.path.join(ROOT, "logs"))
        self.scenario = scenario
        self.debug_images = False
        self.simulated = True

    def sleep(self, ms):
        self.check()

    def click(self, x, y, label="", score=None, wait_ms=None, style="click"):
        super().click(x, y, label, score, wait_ms, style)
        self.scenario.on_action(label)

    def drag(self, x1, y1, x2, y2, label=""):
        super().drag(x1, y1, x2, y2, label)
        self.scenario.on_action(label)


def main() -> int:
    setup_logging(os.path.join(ROOT, "logs"))
    cfg = Config(os.path.join(ROOT, "config.json")).get()
    cfg["economy"]["starting_money"] = 83
    cfg["behavior"]["sync_money_with_ocr"] = False
    cfg["behavior"]["auto_close_info_key"] = None
    cfg["debug_images"] = False
    cfg["timing"] = {k: 0 for k in cfg["timing"]}

    store = TemplateStore(cfg, ROOT)
    reader = MoneyReader(cfg, store)
    scenario = Scenario()
    act = SimActions(cfg, threading.Event(), scenario)
    state = State(running=True, dry_run=True, started_at=0.0)

    # --- vision assertions -------------------------------------------------
    for stage, expected in [
        ("closed", {"market_open", "money", "drag_item", "drop_slot"}),
        ("market", {"market_open", "refresh", "buy", "money"}),
        ("cards", {"dup_card"}),
        ("info", {"sell"}),
    ]:
        found, _ = store.probe(scenario.frames[stage])
        names = {m.name for m in found}
        assert expected <= names, f"{stage}: missing {expected - names}, got {names}"
    assert reader.read(scenario.frames["market"]).value == 83, "money OCR should read 83"
    assert reader.read(scenario.frames["info"]).value is None, "money hidden behind card info"

    # the sell bar prints the card's own price (+5, +44 for a merged card, ...), so the
    # template has to be cropped to the 出售 label: paint over the number, it still hits
    repriced = scenario.frames["info"].image.copy()
    cv2.rectangle(repriced, (2200, 1188), (2312, 1252), (28, 18, 38), -1)
    hit = store.find(Frame(repriced, (0, 0), "repriced"), "sell")
    assert hit is not None, "the sell button must not depend on the price digits"
    assert abs(hit.x - store.find(scenario.frames["info"], "sell").x) <= 8, \
        "the price change must not move the 出售 click point"

    # the shop overlay repaints the coin icon, so the anchor can be missing:
    # the fixed corner ROI must still read the number
    obscured = scenario.frames["market"].image[:, :2560].copy()
    cv2.rectangle(obscured, (2060, 1180), (2162, 1282), (45, 32, 60), -1)
    assert reader.read(Frame(obscured, (0, 0), "obscured")).value == 83, \
        "fixed corner ROI must read the money when the coin anchor is gone"
    print("vision assertions: OK")

    # --- sell-zone guard (ROIs disabled: middle/bench card is a false positive) ---
    guard_cfg = copy.deepcopy(cfg)
    guard_cfg["match"]["use_roi"] = False
    guard_store = TemplateStore(guard_cfg, ROOT)
    guard_bot = Bot(Config(os.path.join(ROOT, "config.json")), scenario, guard_store,
                    reader, act, state, guard_cfg, 1)
    closed = scenario.frames["closed"]
    slot = guard_store.find(closed, "drop_slot")
    middle = guard_store.find_all(closed, "dup_card", 0.62)
    assert any(m.y < guard_bot.sell_zone_min_y(closed) for m in middle), \
        "expected the bench/drop-slot card to be a low-score false positive"
    assert guard_bot.sellable_cards(closed, slot) == [], \
        "middle-screen drop-slot card must never be sellable"
    assert len(guard_bot.sellable_cards(scenario.frames["cards"], slot)) == 2, \
        "both bottom-strip duplicated cards must stay sellable"
    print("sell-zone guard assertions: OK")

    # --- panel that opens then closes again in one click --------------------
    flick = FlickerScenario()
    flick_act = SimActions(cfg, threading.Event(), flick)
    flick_state = State(running=True, dry_run=True, started_at=0.0)
    flick_bot = Bot(Config(os.path.join(ROOT, "config.json")), flick, store,
                    reader, flick_act, flick_state, cfg, 1)
    hit = flick_bot.open_market()
    assert flick.stage == "market", "open_market must retry until the panel stays"
    assert hit.name == "refresh", "open_market must return the refresh button"
    print("market reopen assertions: OK")

    # --- projector already in the bag: task1 must not spend anything ----------
    stock = Scenario()
    stock.stage = "closed_full"            # item grid still shows 完美投影仪
    stock_state = State(running=True, dry_run=True, started_at=0.0)
    stock_bot = Bot(Config(os.path.join(ROOT, "config.json")), stock, store, reader,
                    SimActions(cfg, threading.Event(), stock), stock_state, cfg, 1)
    stock_bot.task1()
    assert stock_state.buys == 0 and stock_state.refreshes == 0, \
        "with a projector in the grid the buy loop must be skipped"
    print("skip-buy-with-stock assertions: OK")

    # --- state machine ------------------------------------------------------
    Bot(Config(os.path.join(ROOT, "config.json")), scenario, store, reader,
        act, state, cfg, cycles=1).run()

    print("\n--- simulated run ---")
    print(f"money={state.money} buys={state.buys} refreshes={state.refreshes} "
          f"units={state.units} drags={state.drags} sold={state.sold} profit={state.profit}")
    assert state.buys == 9, f"expected 9 packs from 83 money, got {state.buys}"
    assert state.refreshes == 9, f"expected 9 refreshes, got {state.refreshes}"
    assert state.units == 27, f"expected 27 projectors, got {state.units}"
    assert state.drags == 2, f"expected exactly 2 drags, got {state.drags}"
    assert state.sold == 2, f"expected 2 cards sold, got {state.sold}"
    assert state.money == 12, f"expected 83-81+10=12, got {state.money}"
    print("state machine assertions: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
