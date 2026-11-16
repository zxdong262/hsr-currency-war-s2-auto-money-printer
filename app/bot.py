"""Task state machine: buy packs -> duplicate cards -> sell cards."""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

import cv2

from .actions import Actions, StopRequested
from .config import Config
from .logbus import LOG, event
from .vision import (Frame, Match, MoneyReader, ScreenSource, TemplateStore,
                     TextLocator, create_screen)
from .window import (activate_window, find_window, foreground_title, is_elevated,
                     window_elevated)


class BotError(RuntimeError):
    pass


def _pad(box: list[int], margin: float) -> list[int]:
    m = int(margin)
    return [box[0] - m, box[1] - m, box[2] + m, box[3] + m]


def _boxes_overlap(a: list[int], b: list[int]) -> bool:
    return (min(a[2], b[2]) > max(a[0], b[0])
            and min(a[3], b[3]) > max(a[1], b[1]))


@dataclass
class State:
    phase: str = "idle"
    running: bool = False
    dry_run: bool = True
    money: Optional[int] = None
    money_source: str = "-"
    buys: int = 0
    refreshes: int = 0
    drags: int = 0
    drag_attempts: int = 0
    sold: int = 0
    units: int = 0
    cycle: int = 0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    message: str = ""
    last_error: Optional[str] = None
    debug_shots: list[str] = field(default_factory=list)
    pack_price: int = 9
    sell_price: int = 5
    refresh_cost: int = 0

    @property
    def profit(self) -> int:
        return (self.sold * self.sell_price - self.buys * self.pack_price
                - self.refreshes * self.refresh_cost)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["profit"] = self.profit
        d["elapsed_s"] = round(
            (self.finished_at or time.time()) - self.started_at, 1
        ) if self.started_at else 0.0
        return d


class Bot:
    def __init__(self, config: Config, screen: ScreenSource, store: TemplateStore,
                 reader: MoneyReader, act: Actions, state: State, cfg: dict,
                 cycles: int = 1):
        self.config = config
        self.screen = screen
        self.store = store
        self.reader = reader
        self.act = act
        self.s = state
        self.cfg = cfg
        self.mcfg = cfg["money"]
        self.econ = cfg["economy"]
        self.limits = cfg["limits"]
        self.behavior = cfg["behavior"]
        self.poll_s = cfg["match"]["poll_ms"] / 1000.0
        self.wait_timeout = float(cfg["match"]["wait_timeout_s"])
        self.cycles = int(cycles)          # <= 0 means: cycle until the stop hotkey

    # ------------------------------------------------------------- primitives --

    def frame(self) -> Frame:
        self.act.check()
        return self.screen.grab()

    def find(self, name: str, frame: Optional[Frame] = None,
             threshold: Optional[float] = None) -> Optional[Match]:
        return self.store.find(frame or self.frame(), name, threshold)

    def find_all(self, name: str, frame: Optional[Frame] = None,
                 threshold: Optional[float] = None) -> list[Match]:
        return self.store.find_all(frame or self.frame(), name, threshold)

    def wait_for(self, name: str, timeout_s: Optional[float] = None,
                 label: str = "") -> Match:
        timeout = self.wait_timeout if timeout_s is None else timeout_s
        end = time.monotonic() + timeout
        last = 0.0
        tries = 0
        while True:
            self.act.check()
            f = self.frame()
            m = self.store.find(f, name)
            tries += 1
            if m:
                event("FOUND", action="found", name=name, x=m.x, y=m.y,
                      score=round(m.score, 4), tries=tries, waited_s=round(time.monotonic() - (end - timeout), 2))
                return m
            score = self.store.score(f, name)
            if score > last + 0.02:
                LOG.debug("waiting for %s, best score %.3f", name, score)
                last = score
            if time.monotonic() >= end:
                raise BotError(
                    f"'{name}' not found within {timeout:.1f}s "
                    f"(best score {score:.3f}, threshold {self.store.templates[name].threshold})")
            self.act.sleep(self.poll_s * 1000)

    def wait_for_burst(self, name: str, timeout_s: float, tag: str,
                       frames: int = 4) -> Match:
        """Like wait_for, but saves the first few frames so a panel that flashes
        open and immediately closed can be seen in the debug screenshots."""
        end = time.monotonic() + timeout_s
        saved = 0
        last = 0.0
        while True:
            self.act.check()
            f = self.frame()
            m = self.store.find(f, name)
            if m:
                if saved == 0:
                    self.snap(f"{tag}-ok", f, [m])
                event("FOUND", action="found", name=name, x=m.x, y=m.y,
                      score=round(m.score, 4), waited_s=round(timeout_s - (end - time.monotonic()), 2))
                return m
            if saved < frames:
                saved += 1
                self.snap(f"{tag}-{saved}", f)
            score = self.store.score(f, name)
            if score > last + 0.02:
                LOG.debug("waiting for %s (%s), best score %.3f", name, tag, score)
                last = score
            if time.monotonic() >= end:
                raise BotError(f"'{name}' not found within {timeout_s:.1f}s after '{tag}'")
            self.act.sleep(min(self.poll_s * 1000, 160))

    def snap(self, tag: str, frame: Frame, matches: Optional[list[Match]] = None) -> None:
        name = self.act.screenshot(tag, frame, self.store, matches)
        if name:
            self.s.debug_shots.append(name)
            if len(self.s.debug_shots) > 60:
                self.s.debug_shots.pop(0)

    # ------------------------------------------------------- sell-zone guard --

    def sell_zone_min_y(self, frame: Frame) -> int:
        """Top edge of the bottom strip where duplicated cards are allowed."""
        zone = self.behavior.get("sell_zone") or {}
        frac = min(0.95, max(0.0, float(zone.get("bottom_fraction", 0.20))))
        return int(frame.origin[1] + frame.image.shape[0] * (1.0 - frac))

    def bench_slots(self, frame: Frame) -> list[dict]:
        """The 备战席 strip along the bottom, slot by slot, with a brightness read.

        Duplicated cards are character portraits we cannot pre-capture, so the strip
        is located geometrically (fixed slots) and occupancy is just brightness:
        an empty slot measures ~32, a card inside it ~120+.
        """
        bench = self.behavior.get("bench") or {}
        height = frame.image.shape[0]
        scale = height / float(bench.get("ref_height", 1440))
        x0 = float(bench.get("x0", 508)) * scale
        pitch = float(bench.get("pitch", 166.4)) * scale
        slot_w = float(bench.get("width", 152)) * scale
        y0 = float(bench.get("y0", 1128)) * scale
        slot_h = float(bench.get("height", 172)) * scale
        pad = float(bench.get("inner_pad", 16)) * scale
        count = int(bench.get("count", 9))

        out: list[dict] = []
        for i in range(count):
            left = int(round(x0 + pitch * i))
            box = [left, int(round(y0)), int(round(left + slot_w)), int(round(y0 + slot_h))]
            if box[2] >= frame.image.shape[1] or box[3] >= frame.image.shape[0]:
                break
            inner = frame.image[box[1] + int(pad):box[3] - int(pad),
                                box[0] + int(pad):box[2] - int(pad)]
            if inner.size == 0:
                break
            gray = cv2.cvtColor(inner, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)
            colorful = float(((hsv[..., 1] > 70) & (hsv[..., 2] > 70)).mean())
            out.append({"index": i, "box": box,
                        "mean": round(float(gray.mean()), 1),
                        "colorful": round(colorful, 3)})
        return out

    def bench_cards(self, frame: Frame) -> list[Match]:
        """Occupied 备战席 slots, left to right."""
        bench = self.behavior.get("bench") or {}
        need = float(bench.get("occupied_mean", 55))
        colorful_min = float(bench.get("colorful_min", 0.15))
        cards: list[Match] = []
        for s in self.bench_slots(frame):
            # a white tooltip or a bright popup is bright but colourless, a card
            # portrait is very colourful - use both, otherwise a screenshot tip
            # lying over the strip counts as two duplicated cards
            if s["mean"] < need or s["colorful"] < colorful_min:
                continue
            x0, y0, x1, y1 = s["box"]
            cards.append(Match(name="bench_card", score=1.0, scale=1.0,
                               x=(x0 + x1) // 2, y=(y0 + y1) // 2,
                               left=x0, top=y0, width=x1 - x0, height=y1 - y0))
        return cards

    def bench_strip_gray(self, frame: Frame):
        """Grayscale image of the whole 备战席 strip, for before/after comparison.

        The strip is pixel-stable while idle (measured max delta 1 over 0.7 s), so
        any real change - a new copy *or* three copies merging into a 2-star card,
        which does not change the slot count - shows up as a clear diff.
        """
        bench = self.behavior.get("bench") or {}
        height, width = frame.image.shape[:2]
        s = height / float(bench.get("ref_height", 1440))
        x0 = int(float(bench.get("x0", 508)) * s)
        pitch = float(bench.get("pitch", 166.4)) * s
        y0 = int(float(bench.get("y0", 1128)) * s)
        slot_w = float(bench.get("width", 152)) * s
        slot_h = float(bench.get("height", 172)) * s
        count = int(bench.get("count", 9))
        x1 = min(int(x0 + pitch * (count - 1) + slot_w), width)
        y1 = min(int(y0 + slot_h), height)
        if x1 - x0 < 40 or y1 - y0 < 40:
            return None
        return cv2.cvtColor(frame.image[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)

    def bench_strip_changed(self, before, after) -> float:
        """Fraction of strip pixels that changed by more than 8 grey levels."""
        if before is None or after is None or before.shape != after.shape:
            return 1.0
        d = cv2.absdiff(before, after)
        return float((d > 8).mean())

    def wait_for_copy(self, cards_before: int, strip_before, timeout_s: float) -> bool:
        """True once the 备战席 reacted to one drag."""
        need = float((self.behavior.get("task2") or {}).get("strip_change_min", 0.004))
        end = time.monotonic() + timeout_s
        while True:
            self.act.check()
            f = self.frame()
            if len(self.bench_cards(f)) > cards_before:
                return True
            if self.bench_strip_changed(strip_before, self.bench_strip_gray(f)) >= need:
                return True
            if time.monotonic() >= end:
                return False
            self.act.sleep(120)

    def sellable_cards(self, frame: Frame, slot: Optional[Match] = None) -> list[Match]:
        """Cards sitting in the bottom 备战席 strip, never the board cards above it."""
        zone = self.behavior.get("sell_zone") or {}
        enforce = bool(zone.get("enable", True))
        min_y = self.sell_zone_min_y(frame)
        margin = float(zone.get("reject_near_drop_slot_px", 160)) \
            * self.store.frame_scale(frame)

        cards = self.bench_cards(frame)

        good, rejected = [], []
        for c in cards:
            why = None
            if enforce and c.y < min_y:
                why = f"outside bottom sell zone (y={c.y} < {min_y})"
            elif slot and abs(c.x - slot.x) <= margin and abs(c.y - slot.y) <= margin:
                why = f"too close to drop slot ({slot.x},{slot.y})"
            elif slot and _boxes_overlap(c.box, _pad(slot.box, margin)):
                why = "overlaps drop-slot region"
            if why:
                rejected.append({"x": c.x, "y": c.y,
                                 "score": round(c.score, 3), "reason": why})
            else:
                good.append(c)

        if rejected:
            event("SELL ZONE REJECT", action="guard", level=30, min_y=min_y,
                  bottom_fraction=zone.get("bottom_fraction", 0.20),
                  rejected=rejected,
                  note="kept out of the middle/bench area on purpose")
        return sorted(good, key=lambda m: (m.x, -m.score))

    # ------------------------------------------------------------------ money --

    def read_money(self, reason: str, allow_increase: bool = True,
                   max_jump: Optional[int] = None) -> Optional[int]:
        f = self.frame()
        r = self.reader.read(f)
        before = self.s.money
        if max_jump is None:
            max_jump = int(self.mcfg.get("max_jump", 60))
        # While buying, the counter can only go down: a misread that is higher
        # than the ledger would keep the buy loop running on money that is gone.
        if (r.value is not None and not allow_increase and before is not None
                and r.value > before):
            event("MONEY SUSPECT", action="money", reason=reason, ocr_value=r.value,
                  ocr_raw=r.raw, backend=r.backend, ledger=before, level=30,
                  note="counter cannot grow while buying; keeping the ledger value")
            return self.s.money
        if r.value is not None and (before is None or abs(r.value - before) <= max_jump):
            self.s.money = r.value
            self.s.money_source = r.backend
            if before is not None and before != r.value:
                event("MONEY SYNC", action="money", reason=reason, **{"from": before, "to": r.value},
                      ocr_raw=r.raw, backend=r.backend)
            else:
                event("MONEY READ", action="money", reason=reason, value=r.value,
                      ocr_raw=r.raw, backend=r.backend)
        else:
            if self.s.money is None:
                self.s.money = self.econ.get("starting_money")
                self.s.money_source = "starting_money" if self.s.money is not None else "unknown"
            event("MONEY LEDGER", action="money", reason=reason, value=self.s.money,
                  ocr_value=r.value, ocr_backend=r.backend,
                  note="OCR unavailable/implausible; keeping ledger value", level=30)
        return self.s.money

    def read_money_retry(self, reason: str, tries: int = 3, delay_ms: int = 400) -> Optional[int]:
        """Read the counter, retrying while a panel is still animating in."""
        value = None
        for i in range(max(1, tries)):
            value = self.read_money(reason if i == 0 else f"{reason} (retry {i + 1})")
            if value is not None:
                return value
            if i + 1 < tries:
                self.act.sleep(delay_ms)
        return value

    def debit(self, amount: int, reason: str) -> None:
        if amount and self.s.money is not None:
            self.s.money = max(0, self.s.money - amount)
            event("LEDGER -", action="ledger", delta=-amount, reason=reason, money=self.s.money)

    def credit(self, amount: int, reason: str) -> None:
        if amount and self.s.money is not None:
            self.s.money += amount
            event("LEDGER +", action="ledger", delta=amount, reason=reason, money=self.s.money)

    # ------------------------------------------------------------------ task 1 --

    def find_market_button(self, frame: Frame) -> Optional[Match]:
        """Locate the shop entry button.

        Preferred: read the "黑市主理人" label with OCR and click the icon to the
        right of it.  That icon sits in a scrolling list whose backdrop changes
        as the list scrolls, so the icon template score collapses even though
        nothing moved - while the label keeps its position relative to the icon.
        Fallback: the old icon template, for layouts where the label is missing.
        """
        spec = ((self.cfg["templates"].get("market_open") or {}).get("label") or {})
        if spec.get("enable", True):
            keywords = spec.get("keywords") or ["黑市"]
            hit = TextLocator(spec).find(frame, keywords, spec.get("roi"))
            if hit is not None:
                # offsets are calibrated at ref_height and scale with the UI
                scale = frame.image.shape[0] / float(spec.get("ref_height", 1440))
                dx, dy = [float(v) for v in (spec.get("icon_offset") or [180, 19])]
                x = int(round(hit.left + dx * scale))
                y = int(round((hit.top + hit.bottom) / 2.0 + dy * scale))
                event("MARKET LABEL", action="open", level=20, text=hit.text,
                      score=round(hit.score, 3), label_box=[hit.left, hit.top,
                                                            hit.right, hit.bottom],
                      click=[x, y],
                      note="label found by OCR; the icon right of it is clicked")
                return Match(name="market_label", score=hit.score, scale=scale,
                             x=x, y=y, left=hit.left, top=hit.top,
                             width=max(1, hit.width), height=max(1, hit.height))
        m = self.store.find(frame, "market_open")
        if m is not None:
            event("MARKET ICON", action="open", level=20,
                  x=m.x, y=m.y, score=round(m.score, 4),
                  note="label OCR did not match; used the icon template")
        return m

    def open_market(self) -> Match:
        """Click the market icon and make sure the shop panel really stays open.

        A plain click can open *and* close the panel in one go: the shop slides
        over the screen on mouse-down, so the mouse-up lands on the backdrop
        that closes it.  Hence several click styles, each verified by looking
        for the refresh button.
        """
        opts = self.behavior.get("market_open") or {}
        styles = [str(s) for s in (opts.get("styles") or ["away", "click", "hold"])]
        timeout = float(opts.get("verify_timeout_s", 2.5))
        burst = int(opts.get("burst_frames", 4))
        last_err: Optional[Exception] = None
        cached: Optional[Match] = None     # label OCR is slow; reuse it across styles

        for i, style in enumerate(styles, 1):
            self.act.check()
            f = self.frame()
            refresh = self.store.find(f, "refresh")
            if refresh:
                return refresh
            if cached is None or i > 2:
                cached = self.find_market_button(f)
            m = cached
            if m is None:
                if last_err:
                    raise last_err
                raise BotError(
                    "the shop entry could not be located: the '黑市主理人' label was "
                    "not readable and the click-to-open-market.png icon did not match. "
                    "Make sure the game shows the 备战 stage with the left-hand list "
                    "visible (templates.market_open.label.keywords tunes the label text).")
            self.snap(f"open-market-{style}", f, [m])
            event("OPEN MARKET", action="open", attempt=i, style=style,
                  of=len(styles), x=m.x, y=m.y, score=round(m.score, 4),
                  source=m.name)
            self.act.click_match(m, f"open market via {style}", wait_ms=250, style=style)
            try:
                hit = self.wait_for_burst("refresh", timeout, f"open-{style}", burst)
                if i > 1:
                    event("OPEN MARKET OK", action="open", style=style, attempt=i,
                          note="previous click style did not keep the panel open")
                return hit
            except BotError as exc:
                last_err = exc
                event("OPEN MARKET RETRY", action="open", level=30, style=style, attempt=i,
                      error=str(exc),
                      note="panel did not stay open (a plain click can open then "
                           "immediately close it); trying the next click style")
                if self.act.dry_run and not getattr(self.act, "simulated", False):
                    raise BotError(
                        "dry run: the shop is not open, and dry-run clicks never change "
                        "the screen. Open the shop in the game first, or set "
                        "dry_run: false for a real run.") from exc

        self.snap("open-market-failed", self.frame())
        raise last_err or BotError("market menu would not stay open")

    def scan_buy(self) -> Optional[Match]:
        """Look for the buy card over a few frames instead of just one.

        The shop repaints its cards right after every refresh and the card
        background gradient differs per slot, so a single frame can score just
        under the threshold while the projector is plainly on screen.  Polling a
        couple of frames and keeping the best score removes those misses.
        """
        opts = self.behavior.get("buy_scan") or {}
        frames = max(1, int(opts.get("frames", 3)))
        gap = int(opts.get("gap_ms", 140))
        threshold = self.store.templates["buy"].threshold
        best_score, best_frame = 0.0, None
        for i in range(frames):
            self.act.check()
            f = self.frame()
            m = self.store.find(f, "buy")
            s = m.score if m is not None else self.store.score(f, "buy")
            if s > best_score:
                best_score, best_frame = s, f
            if m is not None:
                if i:
                    event("BUY SCAN", action="buy", frames=i + 1, score=round(s, 4),
                          note="matched on a later frame (the first one was animating)")
                return m
            if i + 1 < frames:
                self.act.sleep(gap)
        margin = float(opts.get("near_miss_margin", 0.06))
        if best_frame is not None and best_score >= threshold - margin:
            self.snap("buy-near-miss", best_frame)
            event("BUY NEAR MISS", action="buy", level=30, best=round(best_score, 3),
                  threshold=threshold,
                  note="the projector may be on screen but scored under the "
                       "threshold; screenshot saved for tuning")
        return None

    def task1(self) -> None:
        self.s.phase = "task1 / open market"
        # A projector icon that is still in the grid means there is stock left:
        # burn it before spending 9 coins on another pack.  The icon disappears
        # from the grid once the count hits zero, so this needs no OCR.
        if self.store.find(self.frame(), "drag_item"):
            event("TASK1 SKIP: projectors already in the bag", phase="task1",
                  money=self.s.money, bought=self.s.units, used=self.s.drags,
                  note="use the projectors we own before buying a new pack")
            return
        event("TASK1 START", phase="task1", money=self.s.money)
        f = self.frame()
        if self.store.find(f, "refresh") is None:
            self.open_market()
        else:
            event("market menu already open", action="skip")

        self.s.phase = "task1 / buy loop"
        money = self.read_money_retry("task1 start")
        if money is None:
            self.snap("money-unreadable", self.frame())
            raise BotError(
                "money is unknown: the counter could not be read "
                "(anchor template and money.fallback_roi_rel both failed). "
                "Set economy.starting_money or use 'Override money' in the console.")
        price = int(self.econ["pack_price"])
        cost_refresh = int(self.econ.get("refresh_cost", 0))
        refused = 0
        # the limits are per cycle: this run keeps cycling until the stop hotkey,
        # so comparing them with the run totals would end task1 after the first lap
        buys0, refreshes0 = self.s.buys, self.s.refreshes
        max_buys = int(self.limits["max_buys"])
        # 0 (or null) means "never stop refreshing": keep rolling the shop until the
        # projector shows up.  Only the money counter ends task1 then.
        max_refreshes = int(self.limits.get("max_refreshes") or 0)
        idle_stop = int(self.behavior.get("stop_after_idle_refreshes") or 0)

        while True:
            self.act.check()
            if self.s.money is None or self.s.money < price:
                event("TASK1 STOP: insufficient money", action="stop", money=self.s.money, price=price)
                break
            if self.s.buys - buys0 >= max_buys:
                event("TASK1 STOP: max_buys reached", action="stop", buys=self.s.buys - buys0)
                break
            if max_refreshes > 0 and self.s.refreshes - refreshes0 >= max_refreshes:
                event("TASK1 STOP: max_refreshes reached", action="stop",
                      refreshes=self.s.refreshes - refreshes0, limit=max_refreshes)
                break
            if idle_stop > 0 and self.s.refreshes - refreshes0 > idle_stop \
                    and self.s.buys == buys0:
                event("TASK1 STOP: target never appeared after %d refreshes" % idle_stop,
                      action="stop", level=30, refreshes=self.s.refreshes - refreshes0)
                break

            buy = self.scan_buy()
            if buy is None and self.s.buys == buys0:
                n = self.s.refreshes - refreshes0
                if n and n % 25 == 0:
                    # unlimited refreshing must not go silent: a stuck shop looks
                    # exactly like bad luck, so report the current best score
                    f = self.frame()
                    event("TASK1 IDLE: no projector yet", action="refresh", level=20,
                          refreshes=n, money=self.s.money,
                          buy_score=round(float(self.store.score(f, "buy")), 3),
                          note="refreshing is unlimited (limits.max_refreshes=0); "
                               "still rolling the shop")
            if buy:
                self.snap("buy-target", self.frame(), [buy])
                before = self.s.money
                self.act.click_match(buy, "buy 完美投影仪 pack")
                self.debit(price, "buy pack")
                self.s.buys += 1
                self.s.units += int(self.econ["units_per_pack"])
                event("BUY", action="buy", pack=self.s.buys, units=self.s.units,
                      money=self.s.money, price=price)
                # step 4: re-read the counter from screen (skipped in dry-run: screen never changes)
                if self.behavior.get("sync_money_with_ocr", True) and not self.act.dry_run:
                    after = self.read_money("after buy", allow_increase=False)
                    if before is not None and after == before:
                        # the click did not cost anything: sold out / cannot afford
                        self.s.buys -= 1
                        self.s.units -= int(self.econ["units_per_pack"])
                        refused += 1
                        event("BUY NOT CONFIRMED", action="buy", level=30,
                              money=after, score=round(buy.score, 4), streak=refused,
                              note="money did not change after the click, so the "
                                   "pack was not bought; not counting it")
                        if refused >= int(self.behavior.get("max_refused_buys", 2)):
                            event("TASK1 STOP: the shop keeps refusing the purchase",
                                  action="stop", level=40, money=after, price=price,
                                  note="the counter says the pack is affordable but the "
                                       "money never goes down - stop instead of "
                                       "refreshing forever")
                            break
                    else:
                        refused = 0
            self.refresh_store(cost_refresh)

        event("TASK1 DONE", phase="task1", buys=self.s.buys,
              refreshes=self.s.refreshes, money=self.s.money, units=self.s.units)

    def refresh_store(self, cost: int) -> None:
        f = self.frame()
        r = self.store.find(f, "refresh")
        if r is None:
            event("REFRESH BUTTON MISSING", action="refresh", level=30,
                  note="shop panel closed itself; re-opening it")
            self.open_market()
            r = self.wait_for("refresh", timeout_s=6, label="refresh button")
        self.act.click_match(r, "refresh store", wait_ms=self.cfg["timing"]["after_refresh_ms"])
        self.s.refreshes += 1
        if cost:
            self.debit(cost, "refresh")
        event("REFRESH", action="refresh", count=self.s.refreshes, money=self.s.money)

    # ------------------------------------------------------------------ task 2 --

    def ensure_shop_closed(self) -> None:
        """The shop panel covers the left column of the item grid - and that is
        exactly where a freshly bought item lands, so the drag source can be
        invisible until the panel is gone."""
        t2 = self.behavior.get("task2") or {}
        key = self.behavior.get("close_key_before_task2") or t2.get("close_key")
        if self.store.find(self.frame(), "refresh") is None:
            return
        if not key:
            event("SHOP STILL OPEN", action="close", level=30,
                  note="close_key_before_task2 is empty: the shop panel hides the item grid")
            return
        self.act.press(key, "close the shop panel before duplicating")
        checks = max(1, int(float(t2.get("close_timeout_s", 3.0)) / 0.15))
        for _ in range(checks):
            self.act.check()
            if self.store.find(self.frame(), "refresh") is None:
                event("SHOP CLOSED", action="close", key=key)
                return
            self.act.sleep(150)
        event("SHOP DID NOT CLOSE", action="close", level=30, key=key,
              note="the refresh button is still visible after pressing the close key")

    def find_drag_item(self) -> Match:
        """The item grid scrolls, and the projector is not always in view."""
        t2 = self.behavior.get("task2") or {}
        try:
            return self.wait_for("drag_item", timeout_s=float(t2.get("find_timeout_s", 4.0)),
                                 label="projector item")
        except BotError as exc:
            first = exc
        sc = t2.get("scroll_inventory") or {}
        if sc.get("enable", True):
            # point is calibrated at display.ref_height; scale it with the UI
            ui_scale = self.store.frame_scale(self.frame())
            px, py = [int(round(float(v) * ui_scale))
                      for v in (sc.get("point") or [2400, 620])]
            step = max(1, int(sc.get("notches", 3)))
            for i, clicks in enumerate([-step, -step, step * 2, step * 2]):
                self.act.check()
                self.act.scroll(px, py, clicks, f"scroll item grid {i + 1}")
                try:
                    return self.wait_for("drag_item", timeout_s=2.0, label="projector item")
                except BotError:
                    continue
            # searched the whole grid and found nothing: put it back where it
            # started (-step -step +2step +2step = +2step) so the next run does
            # not start from an arbitrary scroll offset
            self.act.scroll(px, py, -step * 2, "scroll item grid back")
        f = self.frame()
        self.snap("drag-item-missing", f)
        score = self.store.score(f, "drag_item")
        raise BotError(
            "the 完美投影仪 icon never showed up in the right-hand item grid "
            f"(best score {score:.3f}, threshold "
            f"{self.store.templates['drag_item'].threshold}). Check that the shop "
            "panel is closed, that the projector really landed in the inventory, "
            "and that the item grid is not scrolled away - then press the start "
            "hotkey again. Screenshot: logs/debug/*drag-item-missing.png")

    def bench_gain(self, before: int) -> int:
        return len(self.bench_cards(self.frame())) - before

    def task2(self) -> None:
        self.s.phase = "task2 / duplicate"
        self.ensure_shop_closed()
        t2 = self.behavior.get("task2") or {}

        src = self.find_drag_item()
        slot: Optional[Match] = None
        try:
            slot = self.wait_for("drop_slot", timeout_s=float(t2.get("slot_timeout_s", 3.0)),
                                 label="card slot")
        except BotError as exc:
            f = self.frame()
            self.snap("drop-slot-missing", f)
            score = self.store.score(f, "drop_slot")
            raise BotError(
                "the card to duplicate (target-to-drop.png) is not on the board "
                f"(best score {score:.3f}, threshold "
                f"{self.store.templates['drop_slot'].threshold}). Put that character "
                "on the board first - front row or back row both work - then press the "
                "start hotkey again. Screenshots: logs/debug/*drop-slot-missing.png"
            ) from exc

        cap = int(self.econ.get("drag_batch_max", 36) or 0) or 36
        f = self.frame()
        self.snap("drag-setup", f, [slot])
        bench_before = len(self.bench_cards(f))
        event("TASK2 START: duplicate batch", phase="task2", cap=cap,
              source=[src.x, src.y], slot=[slot.x, slot.y],
              slot_score=round(slot.score, 4), slot_scale=slot.scale,
              bench_cards=bench_before)

        # Drag as many copies as the bag allows (capped), and only then sell:
        # one long drag run is far fewer mouse ops than drag/sell in pairs.
        verify_s = float(t2.get("drop_verify_s", 1.6))
        made = 0
        attempts = 0
        dead = 0                     # drags in a row that changed nothing
        while made < cap:
            self.act.check()
            f = self.frame()
            s2 = self.store.find(f, "drag_item")
            if s2 is None:
                event("DRAG BATCH STOP: out of projectors", action="duplicate",
                      made=made, attempts=attempts,
                      note="the projector icon is gone from the item grid")
                break
            # the drop card can shift a slot when the board re-lays out
            d2 = self.store.find(f, "drop_slot") or slot
            cards_now = len(self.bench_cards(f))
            strip_now = self.bench_strip_gray(f)
            self.act.drag(s2.x, s2.y, d2.x, d2.y, f"duplicate {made + 1}/{cap}")
            self.s.drag_attempts += 1
            attempts += 1
            if self.wait_for_copy(cards_now, strip_now, verify_s):
                made += 1
                self.s.drags += 1
                dead = 0
                if made == 1 or made % 6 == 0:
                    self.snap(f"drag-{made}", self.frame(), [d2])
                    event("DUPLICATE", action="duplicate", index=made, total=cap,
                          **{"from": [s2.x, s2.y], "to": [d2.x, d2.y]})
            else:
                dead += 1
                event("DROP DID NOT DUPLICATE", action="duplicate", level=30,
                      attempt=attempts, made=made, point=[d2.x, d2.y],
                      note="the 备战席 did not change after this drop; the bench may "
                           "be full or the drop missed the card")
                if dead >= 2:
                    break
        event("DRAG BATCH DONE", action="duplicate", made=made, attempts=attempts,
              bench_cards=len(self.bench_cards(self.frame())))
        self.snap("drag-batch-end", self.frame())

        if made <= 0 and attempts <= 0:
            self.snap("duplicate-failed", self.frame())
            raise BotError(
                "no projector was dragged at all: the 完美投影仪 icon was not in the "
                "item grid, so there was nothing to duplicate. Buy a pack first, then "
                "press the start hotkey again. Screenshot: logs/debug/*duplicate-failed.png")
        if made <= 0:
            self.snap("duplicate-failed", self.frame())
            raise BotError(
                f"{attempts} projector drag(s) onto the card at ({slot.x},{slot.y}) "
                "all failed to change the 备战席 strip. Check that the drag really "
                "started on the projector icon and that the release landed on the "
                "card portrait. Screenshot: logs/debug/*duplicate-failed.png")

        self.s.phase = "task2 / sell"
        zone = self.behavior.get("sell_zone") or {}
        auto_close = self.behavior.get("auto_close_info_key")
        max_sells = int(self.limits["max_sells"])
        min_y = self.sell_zone_min_y(self.frame())
        # count the strip once: the loop below may only sell what was really there,
        # so a false positive can never turn into an endless sell loop
        strip_cards = len(self.sellable_cards(self.frame(), slot))
        to_sell = min(strip_cards, max_sells)
        event("SELL ZONE", action="guard", min_y=min_y,
              bottom_fraction=zone.get("bottom_fraction", 0.20),
              drop_slot=[slot.x, slot.y], duplicated=made, in_strip=strip_cards,
              selling=to_sell,
              note="sell everything in the bottom strip, never a board card")

        sell_timeout = float(t2.get("sell_timeout_s", 3.5))
        skipped: list[int] = []      # x of cards whose panel never opened
        for i in range(to_sell):
            self.act.check()
            f = self.frame()
            # a card-info popup left open would cover the remaining cards
            if self.store.find(f, "sell") is not None and auto_close:
                self.act.press(auto_close, "close lingering card info")
                f = self.frame()

            cards = [c for c in self.sellable_cards(f, slot)
                     if all(abs(c.x - sx) > 40 for sx in skipped)]
            if not cards:
                event("no duplicated card in bottom sell zone; stop selling", action="stop",
                      level=30, sold=self.s.sold, min_y=min_y, skipped=skipped)
                break
            card = cards[0]
            self.snap(f"card-{i + 1}", f, [card])
            sell: Optional[Match] = None
            for attempt, style in enumerate(("click", "hold"), 1):
                self.act.click(card.x, card.y, f"open card info {i + 1} ({style})",
                               card.score, style=style)
                try:
                    sell = self.wait_for("sell", timeout_s=sell_timeout,
                                         label="sell button")
                    break
                except BotError:
                    continue
            if sell is None:
                # one stubborn card must not kill an unattended multi-hour run
                self.snap(f"sell-panel-missing-{i + 1}", self.frame(), [card])
                event("SELL PANEL DID NOT OPEN", action="sell", level=30,
                      index=i + 1, card=[card.x, card.y], tries=2,
                      note="clicked the card twice but no 出售 button appeared; "
                           "skipping this card and trying the next one")
                skipped.append(card.x)
                if auto_close:
                    self.act.press(auto_close, "close card info after a miss")
                continue
            if zone.get("enable", True) and zone.get("guard_sell_button", True) \
                    and sell.y < self.sell_zone_min_y(f):
                event("sell button outside bottom zone; skip", action="guard", level=40,
                      x=sell.x, y=sell.y,
                      min_y=self.sell_zone_min_y(f),
                      note="refusing to sell a panel that is not in the bottom strip")
                break
            self.snap(f"sell-{i + 1}", self.frame(), [sell])
            self.act.click_match(sell, f"sell card {i + 1}",
                                 wait_ms=self.cfg["timing"]["after_sell_ms"])
            self.credit(int(self.econ["card_sell_price"]), "sell duplicated card")
            self.s.sold += 1
            event("SELL", action="sell", index=i + 1, money=self.s.money,
                  price=self.econ["card_sell_price"])

            f2 = self.frame()
            if self.store.find(f2, "sell") is not None and auto_close:
                self.act.press(auto_close, "close card info after sell")
            if self.behavior.get("sync_money_with_ocr", True) and not self.act.dry_run:
                # a merged (2/3-star) copy sells for far more than one 1-star card,
                # so the jump guard has to be wider here than on the buy side
                self.read_money(f"after sell {i + 1}", allow_increase=True,
                                max_jump=int(self.mcfg.get("max_jump_after_sell", 400)))

        event("TASK2 DONE", phase="task2", drags=self.s.drags, sold=self.s.sold,
              money=self.s.money)

    # -------------------------------------------------------------------- run --

    def run(self) -> None:
        """cycles <= 0 means "keep cycling until the stop hotkey"."""
        endless = self.cycles <= 0
        i = 0
        while endless or i < self.cycles:
            self.act.check()
            i += 1
            buys0, sold0 = self.s.buys, self.s.sold
            self.s.cycle = i
            event("CYCLE START", cycle=i, total=None if endless else self.cycles,
                  money=self.s.money, dry_run=self.act.dry_run)
            self.task1()
            self.task2()
            event("CYCLE DONE", cycle=i, money=self.s.money,
                  buys=self.s.buys, sold=self.s.sold, profit=self.s.profit)
            if self.s.buys == buys0 and self.s.sold == sold0:
                try:
                    self.snap("no-progress", self.frame())
                except Exception:      # screen may be a stub (tests)
                    pass
                raise BotError(
                    f"cycle {i} neither bought a pack nor sold a copy (money="
                    f"{self.s.money}). Nothing left to repeat - buy a pack by hand, "
                    "or put more projectors in the inventory, then start again. "
                    "Screenshot: logs/debug/*no-progress.png")


class Runner:
    """Owns the worker thread, the stop flag and the shared state."""

    def __init__(self, config: Config, screen: ScreenSource, store: TemplateStore,
                 reader: MoneyReader, log_dir: str):
        self.config = config
        self.screen = screen
        self.store = store
        self.reader = reader
        self.log_dir = log_dir
        self.s = State()
        self.stop_event = threading.Event()
        self.actions: Optional[Actions] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self, dry_run: Optional[bool] = None, cycles: Optional[int] = None,
              trigger: str = "web") -> bool:
        with self._lock:
            if self.s.running:
                return False
            cfg = self.config.get()
            run_dry = cfg["dry_run"] if dry_run is None else bool(dry_run)
            ncycles = int(cfg["economy"].get("cycles", 1)) if cycles is None else int(cycles)
            self.stop_event = threading.Event()
            self.s = State(
                running=True, dry_run=run_dry, started_at=time.time(), phase="starting",
                pack_price=int(cfg["economy"]["pack_price"]),
                sell_price=int(cfg["economy"]["card_sell_price"]),
                refresh_cost=int(cfg["economy"].get("refresh_cost", 0)),
            )
            self._thread = threading.Thread(
                target=self._work, args=(cfg, run_dry, ncycles, trigger), daemon=True,
                name="bot-worker")
            self._thread.start()
            return True

    # The bot can only see what is on screen. Starting from the web console means
    # the console is on top, so bring the game forward and verify it before acting.
    def _prepare_window(self, cfg: dict, trigger: str, dry_run: bool) -> None:
        gate = (cfg.get("behavior") or {}).get("start") or {}
        title = gate.get("activate_window")

        # UIPI silently drops SendInput aimed at a higher-integrity window, so a
        # non-elevated app cannot click an elevated game. Fail loudly instead of
        # "running" while every click is discarded.
        if not dry_run and title:
            game_admin = window_elevated(title)
            if game_admin and not is_elevated():
                raise BotError(
                    "the game runs as administrator but this app does not, so Windows "
                    "discards every synthetic click. Close this console and start with "
                    "start.bat (it asks for elevation), then press the start hotkey.")

        focused = False
        before = foreground_title()
        if title:
            ok, why = activate_window(title)
            event("WINDOW FOCUS", action="focus", title=title, ok=ok, result=why,
                  foreground_before=before, level=20 if ok else 30)
            focused = ok
            if ok:
                settle = float(gate.get("settle_s", 0.8))
                if settle > 0:
                    time.sleep(settle)

        if not focused:
            delay = float(gate.get("switch_delay_s", 3.0))
            if trigger != "hotkey" and delay > 0:
                event("SWITCH TO THE GAME NOW", action="countdown", seconds=delay,
                      note="no window title configured or focus was refused")
                if self.stop_event.wait(delay):
                    raise StopRequested()
                if title:
                    ok, why = activate_window(title)
                    event("WINDOW FOCUS (retry)", action="focus", ok=ok, result=why,
                          level=20 if ok else 30)

        # If the app started before the game, the capture backend fell back to
        # mss (stale desktop). Upgrade to WGC now that the window exists.
        cap_title = (cfg.get("capture") or {}).get("window")
        if cap_title and type(self.screen).__name__ != "WgcScreen" \
                and find_window(cap_title):
            try:
                new_screen = create_screen(cfg)
                old_screen, self.screen = self.screen, new_screen
                old_screen.close()
                event("capture switched to WGC", action="capture", window=cap_title)
            except Exception as exc:
                event("capture switch failed", action="capture", level=30, error=str(exc))

        if gate.get("preflight", True):
            self._preflight(cfg, bool(title))

    def _preflight(self, cfg: dict, has_title: bool) -> None:
        gate = (cfg.get("behavior") or {}).get("start") or {}
        wait_s = float(gate.get("preflight_timeout_s", 2.5))
        names = ("market_open", "refresh", "buy", "drag_item", "drop_slot", "sell", "money")
        deadline = time.monotonic() + max(0.0, wait_s)
        scores: dict[str, float] = {}
        while True:
            if self.stop_event.is_set():
                raise StopRequested()
            found, scores = self.store.probe(self.screen.grab())
            hits = {m.name for m in found}
            known = hits & set(names)
            if known:
                event("PREFLIGHT OK", action="preflight", hits=sorted(known),
                      foreground=foreground_title())
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)

        top_name, top_score = max(scores.items(), key=lambda kv: kv[1]) if scores else ("-", 0.0)
        need = min(self.store.templates[n].threshold for n in names
                   if n in self.store.templates)
        hint = ("press the start hotkey while the game is in the foreground"
                if not has_title else
                f"set behavior.start.activate_window to the real game window title")
        raise BotError(
            f"no game UI on screen (best match {top_name}={top_score:.2f}, need {need:.2f}); "
            f"foreground window is {foreground_title()!r}. The game window must be visible - "
            f"{hint}, or set match.use_roi=false if the game moved to another monitor")

    def _work(self, cfg: dict, dry_run: bool, cycles: int, trigger: str = "web") -> None:
        act = Actions(cfg, self.stop_event, dry_run, self.log_dir)
        self.actions = act
        s = self.s
        try:
            event("RUN START", action="run", dry_run=dry_run,
                  cycles=cycles if cycles > 0 else "until F7/stop", trigger=trigger,
                  monitor=cfg.get("monitor"), region=cfg.get("region"))
            self._prepare_window(cfg, trigger, dry_run)
            s.phase = "task1 / open market"
            Bot(self.config, self.screen, self.store, self.reader, act, s, cfg, cycles).run()
            s.message = "completed"
            event("RUN COMPLETE", action="run", money=s.money, buys=s.buys,
                  refreshes=s.refreshes, drags=s.drags, sold=s.sold, profit=s.profit)
        except StopRequested:
            s.message = "stopped by user"
            event("RUN STOPPED", action="run", phase=s.phase, level=30)
        except Exception as exc:
            s.message = f"error: {exc}"
            s.last_error = f"{type(exc).__name__}: {exc}"
            LOG.exception("run failed")
            event("RUN ERROR", action="run", phase=s.phase, error=s.last_error, level=40)
        finally:
            act.release()
            s.finished_at = time.time()
            s.running = False
            s.phase = "idle"
            self.actions = None

    def stop(self) -> None:
        self.stop_event.set()
        event("STOP REQUESTED", action="stop")

    def panic(self) -> None:
        self.stop_event.set()
        if self.actions:
            self.actions.release()
        event("PANIC STOP", action="stop", level=30)

    def reload(self, cfg: dict) -> None:
        self.store.cfg = cfg
        self.store.reload()
        self.reader.cfg = cfg["money"]

    def status(self) -> dict:
        cfg = self.config.get()
        d = self.s.as_dict()
        d.update({"hotkeys": cfg["hotkeys"], "config_dry_run": cfg["dry_run"]})
        return d
