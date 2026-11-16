"""Native desktop console for the HSR currency-war money printer.

The old HTML page is optional legacy tooling; everything a player needs is here:
start/stop, live counters, hotkey editing, config values, resolution switching
(with an explicit confirmation) and a diagnostic panel. All user-facing text is
translated through app.i18n; log/event names stay stable in English.
"""
from __future__ import annotations

import json
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.font import Font

from .display import describe as describe_display, profiles_from
from .hotkeys import parse_spec
from .i18n import detect_language, language, phase_text, set_language, t
from .logbus import BUS, event
from .runtime import icon_path

HOTKEY_ROWS = (
    ("start", "hk.start", "hk.start.tip"),
    ("stop", "hk.stop", "hk.stop.tip"),
    ("panic", "hk.panic", "hk.panic.tip"),
    ("toggle", "hk.toggle", "hk.toggle.tip"),
)


def _fmt_value(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return t("yes") if value else t("no")
    return str(value)


class KeyCapture(tk.Toplevel):
    """Small modal dialog: press a key combination, get a `<f6>`-style spec."""

    def __init__(self, master: tk.Tk, initial: str = ""):
        super().__init__(master)
        self.title(t("capture.title"))
        self.transient(master)
        self.resizable(False, False)
        self.value = (initial or "").strip()
        self.accepted = False

        ttk.Label(self, text=t("capture.press"),
                  font=("Segoe UI", 11)).pack(padx=18, pady=(16, 4))
        self.readout = ttk.Label(self, text=self.value or t("capture.none"),
                                 font=("Consolas", 13))
        self.readout.pack(padx=18, pady=4)
        ttk.Label(self, text=t("capture.hint"),
                  foreground="#666666").pack(padx=18, pady=(2, 14))

        self.bind("<KeyPress>", self._on_key)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.geometry("+%d+%d" % (master.winfo_rootx() + 180,
                                  master.winfo_rooty() + 180))
        self.after(50, self.focus_force)
        self.grab_set()

    def _cancel(self) -> None:
        self.value = ""
        self.accepted = False
        self.destroy()

    def _on_key(self, e: tk.Event) -> None:
        if e.keysym == "Escape":
            self._cancel()
            return
        if e.keysym == "Return":
            self.accepted = bool(self.value)
            self.destroy()
            return
        if e.keysym.startswith(("Shift_", "Control_", "Alt_")):
            return
        mods = []
        if e.state & 0x1:
            mods.append("<shift>")
        if e.state & 0x4:
            mods.append("<ctrl>")
        if e.state & 0x8:
            mods.append("<alt>")
        key = e.keysym.lower()
        if len(key) == 1 or (key.startswith("f") and key[1:].isdigit()):
            pass
        elif key not in ("space", "enter", "tab", "pageup", "pagedown",
                         "home", "end", "up", "down", "left", "right",
                         "insert", "delete", "backspace", "esc", "pause"):
            return
        spec = "+".join(mods + ["<" + key + ">"])
        if parse_spec(spec) is None:
            return
        self.value = spec
        self.readout.configure(text=spec)


class App:
    def __init__(self, root: tk.Tk, ctx, open_settings: bool = False):
        self.root = root
        self.ctx = ctx
        self.lang = set_language(ctx.config.get().get("language") or detect_language())
        self.last_log_id = BUS.last_id
        self._last_snapshot = 0.0
        self._snapshot = ctx.snapshot()
        self.cards: dict[str, tk.StringVar] = {}
        self.hotkey_vars: dict[str, tk.StringVar] = {}
        self.value_vars: dict[str, tk.StringVar] = {}
        self.check_vars: dict[str, tk.BooleanVar] = {}
        self.mono = Font(family="Consolas", size=9)

        root.title(t("window.title"))
        try:
            root.iconbitmap(icon_path())
        except Exception:
            pass  # icon is cosmetic; never block the GUI if it is missing
        root.geometry("980x690")
        root.minsize(880, 610)
        self._build()
        self._load_fields()
        self._refresh_log_view()
        if open_settings:
            self.nb.select(self.tab_settings)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(250, self._tick)

    # ------------------------------------------------------------------ ui --

    def _build(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        head = ttk.Frame(root, padding=(12, 10, 12, 6))
        head.grid(row=0, column=0, sticky="ew")
        ttk.Label(head, text="Auto Money Printer",
                  font=("Segoe UI", 15, "bold")).grid(row=0, column=0, sticky="w")
        self.v_state = tk.StringVar(value=t("state.idle"))
        ttk.Label(head, textvariable=self.v_state,
                  font=("Segoe UI", 12, "bold")).grid(row=0, column=1, padx=14, sticky="w")

        ttk.Label(head, text=t("language")).grid(row=0, column=2, padx=(0, 4), sticky="e")
        self.lang_choices = {t("lang.zh"): "zh", t("lang.en"): "en"}
        self.v_language = tk.StringVar(
            value=t("lang.zh") if language() == "zh" else t("lang.en"))
        cmb = ttk.Combobox(head, textvariable=self.v_language,
                           values=list(self.lang_choices), state="readonly", width=9)
        cmb.grid(row=0, column=3, sticky="w")
        cmb.bind("<<ComboboxSelected>>",
                 lambda _e: self._set_language(self.lang_choices[self.v_language.get()]))

        self.v_admin = tk.StringVar(value="")
        ttk.Label(head, textvariable=self.v_admin).grid(row=0, column=4, sticky="e")
        self.v_note = tk.StringVar(value="")
        ttk.Label(head, textvariable=self.v_note, foreground="#777777").grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(4, 0))
        head.columnconfigure(4, weight=1)

        self.nb = ttk.Notebook(root)
        self.nb.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
        self.tab_control = ttk.Frame(self.nb, padding=12)
        self.tab_settings = ttk.Frame(self.nb, padding=12)
        self.tab_log = ttk.Frame(self.nb, padding=12)
        self.tab_diag = ttk.Frame(self.nb, padding=12)
        for tab, key in ((self.tab_control, "tab.control"),
                         (self.tab_settings, "tab.settings"),
                         (self.tab_log, "tab.log"),
                         (self.tab_diag, "tab.diagnostics")):
            self.nb.add(tab, text=t(key))
        self._build_control()
        self._build_settings()
        self._build_log()
        self._build_diag()

    def _set_language(self, lang: str) -> None:
        if lang == self.lang:
            return
        try:
            index = self.nb.index(self.nb.select())
        except Exception:
            index = 0
        self.lang = set_language(lang)
        self.ctx.config.update({"language": lang})
        for child in list(self.root.winfo_children()):
            child.destroy()
        self.cards = {}
        self.hotkey_vars = {}
        self.value_vars = {}
        self.check_vars = {}
        self.root.title(t("window.title"))
        self._build()
        self._load_fields()
        self._refresh_log_view()
        try:
            self.nb.select(index)
        except Exception:
            pass
        event("GUI LANGUAGE CHANGED", action="config", language=lang)

    def _build_control(self) -> None:
        f = self.tab_control
        f.columnconfigure(0, weight=1)
        row = ttk.Frame(f)
        row.grid(row=0, column=0, sticky="ew")
        for col in range(4):
            row.columnconfigure(col, weight=1)
        fields = (("phase", "card.phase"), ("money", "card.money"),
                  ("buys", "card.buys"), ("refreshes", "card.refreshes"),
                  ("drags", "card.drags"), ("sold", "card.sold"),
                  ("profit", "card.profit"), ("elapsed_s", "card.elapsed"))
        for i, (key, label_key) in enumerate(fields):
            card = ttk.LabelFrame(row, text=t(label_key), padding=(10, 7))
            card.grid(row=i // 4, column=i % 4, sticky="ew", padx=4, pady=4)
            var = tk.StringVar(value="-")
            self.cards[key] = var
            ttk.Label(card, textvariable=var, font=("Consolas", 13)).pack(anchor="w")

        btns = ttk.Frame(f)
        btns.grid(row=1, column=0, sticky="ew", pady=(12, 4))
        self.btn_start = ttk.Button(btns, text=t("btn.start"), command=self._start)
        self.btn_stop = ttk.Button(btns, text=t("btn.stop"), command=self._stop)
        self.btn_panic = ttk.Button(btns, text=t("btn.panic"), command=self._panic)
        self.btn_toggle = ttk.Button(btns, text=t("btn.toggle"), command=self._toggle)
        self.btn_focus = ttk.Button(btns, text=t("btn.focus_game"), command=self._focus)
        for i, b in enumerate((self.btn_start, self.btn_stop, self.btn_panic,
                               self.btn_toggle, self.btn_focus)):
            b.grid(row=0, column=i, padx=(0, 6), ipadx=8)

        opts = ttk.Frame(f)
        opts.grid(row=2, column=0, sticky="ew", pady=6)
        dry = tk.BooleanVar(value=bool(self.ctx.config.get()["dry_run"]))
        self.check_vars["dry_run"] = dry
        ttk.Checkbutton(opts, text=t("opt.dry_run"),
                        variable=dry, command=self._toggle_dry).grid(row=0, column=0)
        ttk.Label(opts, text=t("hint.hotkeys_global"),
                  foreground="#777777").grid(row=0, column=1, padx=18)

        ttk.Label(f, text=t("label.recent_events")).grid(
            row=3, column=0, sticky="w", pady=(10, 4))
        box = ttk.Frame(f)
        box.grid(row=4, column=0, sticky="nsew")
        f.rowconfigure(4, weight=1)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.control_log = tk.Text(box, height=9, font=self.mono, wrap="none")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.control_log.yview)
        self.control_log.configure(yscrollcommand=scroll.set)
        self.control_log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

    def _build_settings(self) -> None:
        f = self.tab_settings
        f.columnconfigure(0, weight=1)
        outer = ttk.Frame(f)
        outer.grid(row=0, column=0, sticky="ew")
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)

        hk = ttk.LabelFrame(outer, text=t("box.shortcuts"), padding=10)
        hk.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        for i, (key, label_key, tip_key) in enumerate(HOTKEY_ROWS):
            var = tk.StringVar()
            self.hotkey_vars[key] = var
            ttk.Label(hk, text=t(label_key), width=8).grid(row=i, column=0, sticky="w")
            ttk.Entry(hk, textvariable=var, width=18,
                      font=("Consolas", 10)).grid(row=i, column=1, padx=4)
            ttk.Button(hk, text=t("btn.set_shortcut"), width=7,
                       command=lambda k=key: self._capture_hotkey(k)).grid(
                           row=i, column=2)
            ttk.Label(hk, text=t(tip_key), foreground="#777777").grid(
                row=i, column=3, padx=(8, 0), sticky="w")
        ttk.Label(hk, text=t("hint.shortcut_format"),
                  foreground="#777777").grid(row=len(HOTKEY_ROWS), column=0,
                                             columnspan=4, sticky="w", pady=(8, 0))

        disp = ttk.LabelFrame(outer, text=t("box.display"), padding=10)
        disp.grid(row=0, column=1, sticky="nsew")
        self.v_display = tk.StringVar(value="-")
        ttk.Label(disp, textvariable=self.v_display,
                  font=("Consolas", 10), wraplength=380).grid(row=0, column=0,
                                                             columnspan=3, sticky="w")
        self.res_frame = ttk.Frame(disp)
        self.res_frame.grid(row=1, column=0, columnspan=3, pady=8, sticky="w")
        ttk.Label(disp, text=t("hint.resolution_confirm"),
                  foreground="#777777").grid(row=2, column=0, columnspan=3, sticky="w")

        vals = ttk.LabelFrame(f, text=t("box.values"), padding=10)
        vals.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        for col in (0, 2, 4):
            vals.columnconfigure(col, weight=1)
        spec = (
            ("pack_price", "val.pack_price", 0, 0),
            ("card_sell_price", "val.card_sell_price", 0, 2),
            ("refresh_cost", "val.refresh_cost", 0, 4),
            ("drag_batch_max", "val.drag_batch_max", 1, 0),
            ("max_refreshes", "val.max_refreshes", 1, 2),
            ("max_sells", "val.max_sells", 1, 4),
            ("max_buys", "val.max_buys", 2, 0),
            ("max_drag_attempts", "val.max_drag_attempts", 2, 2),
            ("cycles", "val.cycles", 2, 4),
            ("bottom_fraction", "val.bottom_fraction", 3, 0),
            ("stop_after_idle_refreshes", "val.stop_after_idle_refreshes", 3, 2),
        )
        for key, label_key, r, c in spec:
            var = tk.StringVar()
            self.value_vars[key] = var
            ttk.Label(vals, text=t(label_key)).grid(row=r, column=c, sticky="w", pady=3)
            ttk.Entry(vals, textvariable=var, width=12,
                      font=("Consolas", 10)).grid(row=r, column=c + 1, sticky="w",
                                                  padx=8, pady=3)
        self.check_vars["debug_images"] = tk.BooleanVar(value=False)
        ttk.Checkbutton(vals, text=t("val.debug_images"),
                        variable=self.check_vars["debug_images"]).grid(
                            row=3, column=4, sticky="w")

        actions = ttk.Frame(f)
        actions.grid(row=2, column=0, sticky="ew", pady=(12, 4))
        ttk.Button(actions, text=t("btn.save_settings"), command=self._save).grid(
            row=0, column=0, ipadx=8)
        ttk.Button(actions, text=t("btn.reload_file"), command=self._load_fields).grid(
            row=0, column=1, padx=8, ipadx=8)
        ttk.Button(actions, text=t("btn.open_config"), command=self._open_config).grid(
            row=0, column=2, ipadx=8)
        self.v_settings = tk.StringVar(value="")
        ttk.Label(actions, textvariable=self.v_settings,
                  foreground="#777777").grid(row=0, column=3, padx=10)

    def _build_log(self) -> None:
        f = self.tab_log
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        bar = ttk.Frame(f)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(bar, text=t("log.level")).grid(row=0, column=0)
        self.log_filter = tk.StringVar(value="ALL")
        ttk.Combobox(bar, textvariable=self.log_filter, width=9,
                     values=("ALL", "INFO", "WARNING", "ERROR"),
                     state="readonly").grid(row=0, column=1, padx=6)
        self.log_filter.trace_add("write", lambda *_: self._refresh_log_view())
        ttk.Button(bar, text=t("btn.open_log"),
                   command=self._open_log).grid(row=0, column=2, padx=8)
        ttk.Button(bar, text=t("btn.open_debug"),
                   command=self._open_debug).grid(row=0, column=3)

        box = ttk.Frame(f)
        box.grid(row=1, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.full_log = tk.Text(box, font=self.mono, wrap="none")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.full_log.yview)
        self.full_log.configure(yscrollcommand=scroll.set)
        self.full_log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

    def _build_diag(self) -> None:
        f = self.tab_diag
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        self.v_diag = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.v_diag, font=("Consolas", 10),
                  justify="left").grid(row=0, column=0, sticky="w")
        bar = ttk.Frame(f)
        bar.grid(row=2, column=0, sticky="ew", pady=8)
        ttk.Button(bar, text=t("btn.probe"), command=self._probe).grid(row=0, column=0)
        ttk.Button(bar, text=t("btn.open_log_folder"),
                   command=self._open_log).grid(row=0, column=1, padx=8)
        ttk.Button(bar, text=t("btn.refresh"), command=self._snapshot_now).grid(
            row=0, column=2)
        box = ttk.Frame(f)
        box.grid(row=1, column=0, sticky="nsew", pady=8)
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.probe_out = tk.Text(box, font=self.mono, wrap="none")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.probe_out.yview)
        self.probe_out.configure(yscrollcommand=scroll.set)
        self.probe_out.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

    # ------------------------------------------------------------- config --

    def _load_fields(self) -> None:
        cfg = self.ctx.config.get()
        for key in self.hotkey_vars:
            self.hotkey_vars[key].set(cfg["hotkeys"].get(key, ""))
        econ, lim = cfg["economy"], cfg["limits"]
        values = {
            "pack_price": econ.get("pack_price"),
            "card_sell_price": econ.get("card_sell_price"),
            "refresh_cost": econ.get("refresh_cost"),
            "drag_batch_max": econ.get("drag_batch_max"),
            "max_refreshes": lim.get("max_refreshes"),
            "max_sells": lim.get("max_sells"),
            "max_buys": lim.get("max_buys"),
            "max_drag_attempts": lim.get("max_drag_attempts"),
            "cycles": econ.get("cycles"),
            "bottom_fraction": (cfg["behavior"].get("sell_zone") or {}).get("bottom_fraction"),
            "stop_after_idle_refreshes": cfg["behavior"].get("stop_after_idle_refreshes"),
        }
        for key, var in self.value_vars.items():
            var.set(_fmt_value(values.get(key)))
        self.check_vars["dry_run"].set(bool(cfg.get("dry_run")))
        self.check_vars["debug_images"].set(bool(cfg.get("debug_images")))
        self._refresh_display_buttons()
        self.v_settings.set("")

    def _capture_hotkey(self, key: str) -> None:
        dlg = KeyCapture(self.root, self.hotkey_vars[key].get())
        self.root.wait_window(dlg)
        if dlg.accepted:
            self.hotkey_vars[key].set(dlg.value)
            self.v_settings.set(t("settings.unsaved"))

    def _save(self) -> None:
        try:
            ints = {k: int(self.value_vars[k].get().strip()) for k in (
                "pack_price", "card_sell_price", "refresh_cost", "drag_batch_max",
                "max_refreshes", "max_sells", "max_buys", "max_drag_attempts",
                "cycles", "stop_after_idle_refreshes")}
            frac = float(self.value_vars["bottom_fraction"].get().strip())
            if not 0.0 <= frac <= 0.95:
                raise ValueError(t("err.sell_zone"))
        except ValueError as exc:
            messagebox.showerror(t("err.invalid_value"), str(exc), parent=self.root)
            return
        hotkeys = {}
        for key, var in self.hotkey_vars.items():
            spec = var.get().strip().lower().replace(" ", "")
            if spec and parse_spec(spec) is None:
                messagebox.showerror(
                    t("err.invalid_shortcut"),
                    t("err.shortcut_detail", spec=spec, key=key), parent=self.root)
                return
            hotkeys[key] = spec
        patch = {
            "hotkeys": hotkeys,
            "dry_run": bool(self.check_vars["dry_run"].get()),
            "debug_images": bool(self.check_vars["debug_images"].get()),
            "economy": {
                "pack_price": ints["pack_price"],
                "card_sell_price": ints["card_sell_price"],
                "refresh_cost": ints["refresh_cost"],
                "drag_batch_max": ints["drag_batch_max"],
                "cycles": ints["cycles"],
            },
            "limits": {
                "max_refreshes": ints["max_refreshes"],
                "max_sells": ints["max_sells"],
                "max_buys": ints["max_buys"],
                "max_drag_attempts": ints["max_drag_attempts"],
            },
            "behavior": {
                "sell_zone": {"bottom_fraction": frac},
                "stop_after_idle_refreshes": ints["stop_after_idle_refreshes"],
            },
        }
        try:
            self.ctx.apply_config(patch)
        except Exception as exc:
            messagebox.showerror(t("err.save_failed"), str(exc), parent=self.root)
            return
        self.v_settings.set(t("settings.saved"))
        self._refresh_display_buttons()
        event("GUI SETTINGS SAVED", action="config", hotkeys=hotkeys,
              economy=patch["economy"])

    def _toggle_dry(self) -> None:
        value = bool(self.check_vars["dry_run"].get())
        self.ctx.apply_config({"dry_run": value})
        self.v_settings.set(t("settings.saved"))

    def _refresh_display_buttons(self) -> None:
        cfg = self.ctx.config.get()
        d = describe_display(cfg)
        mode = t("display.supported") if d["supported"] else t("display.custom")
        self.v_display.set(t("display.line", w=d["current"][0], h=d["current"][1],
                             scale=d["scale"], mode=mode))
        for child in list(self.res_frame.children.values()):
            child.destroy()
        for i, (w, h) in enumerate(profiles_from(cfg)):
            ttk.Button(self.res_frame, text=f"{w} x {h}", width=14,
                       command=lambda ww=w, hh=h: self._switch_resolution(ww, hh)).grid(
                           row=0, column=i, padx=(0, 6))

    def _switch_resolution(self, width: int, height: int) -> None:
        cfg = self.ctx.config.get()
        if (cfg.get("display") or {}).get("ask_before_change", True):
            ok = messagebox.askyesno(
                t("dlg.change_resolution"),
                t("dlg.change_resolution_msg",
                  current=describe_display(cfg)["current"], width=width, height=height),
                parent=self.root)
            if not ok:
                return
        result = self.ctx.change_resolution(width, height, confirmed=True)
        if not result.get("ok"):
            messagebox.showerror(t("err.resolution_failed"),
                                 result.get("message", t("err.unknown")),
                                 parent=self.root)
            return
        self.v_settings.set(result.get("message", ""))
        self.root.after(1800, self._refresh_display_buttons)

    # ------------------------------------------------------------ actions --

    def _start(self) -> None:
        if not self.ctx.runner.start(trigger="gui"):
            self.v_note.set(t("note.already_running"))

    def _stop(self) -> None:
        self.ctx.runner.stop()
        self.v_note.set(t("note.stop_requested"))

    def _panic(self) -> None:
        self.ctx.runner.panic()
        self.v_note.set(t("note.panic_stop"))

    def _toggle(self) -> None:
        self.ctx.hotkeys_toggle()

    def _focus(self) -> None:
        ok, why = self.ctx.focus_game()
        self.v_note.set(t("note.focus", why=why))

    def _on_close(self) -> None:
        if self.ctx.runner.s.running and not messagebox.askyesno(
                t("dlg.quit"), t("dlg.quit_msg"), parent=self.root):
            return
        self.ctx.runner.stop()
        self.ctx.shutdown()
        self.root.destroy()

    # --------------------------------------------------------------- logs --

    @staticmethod
    def _format_log(item: dict) -> str:
        ts = item.get("ts", "")
        ms = item.get("ms", 0)
        data = item.get("data") or {}
        msg = item.get("msg", "")
        if data:
            msg += "  " + json.dumps(data, ensure_ascii=False, default=str)[:240]
        return f"{ts}.{int(ms):03d} {item.get('level', ''):<7} {msg}\n"

    def _refresh_log_view(self) -> None:
        level = self.log_filter.get()
        self.full_log.delete("1.0", "end")
        for item in BUS.snapshot(600):
            if level != "ALL" and item.get("level") != level:
                continue
            self.full_log.insert("end", self._format_log(item))
        self.full_log.see("end")

    def _append_log(self, item: dict) -> None:
        level = self.log_filter.get()
        if level == "ALL" or item.get("level") == level:
            line = self._format_log(item)
            self.full_log.insert("end", line)
            if int(self.full_log.index("end-1c").split(".")[0]) > 700:
                self.full_log.delete("1.0", "100.0")
            self.full_log.see("end")
        line = self._format_log(item)
        self.control_log.insert("end", line)
        if int(self.control_log.index("end-1c").split(".")[0]) > 200:
            self.control_log.delete("1.0", "50.0")
        self.control_log.see("end")

    # --------------------------------------------------------- diagnostics --

    def _snapshot_now(self) -> None:
        try:
            self._snapshot = self.ctx.snapshot()
            self._last_snapshot = time.time()
        except Exception as exc:
            self.v_diag.set(t("diag.failed", error=exc))
            return
        s = self._snapshot
        rt = getattr(self.ctx, "runtime", None)
        lines = [
            f"{t('diag.state'):<14} {phase_text(s.get('phase'))} / "
            f"{t('diag.running') if s.get('running') else t('diag.idle')}",
            f"{t('diag.screen'):<14} {s['display']['current'][0]}x{s['display']['current'][1]}  "
            f"scale {s['display']['scale']}",
            f"{t('diag.capture'):<14} {s.get('capture')}",
            f"{t('diag.admin'):<14} {s.get('elevated')}   "
            f"{t('diag.game_admin')}: {s.get('game_elevated')}",
            f"{t('diag.game_window'):<14} {s.get('game_window')}",
            f"{t('diag.hotkeys'):<14} {s.get('hotkeys_enabled')}  "
            f"{t('diag.backends')}={s.get('hotkey_backends')}",
            f"{t('diag.config'):<14} {getattr(rt, 'config_path', self.ctx.config.path)}",
            f"{t('diag.templates'):<14} {self.ctx.store.tpl_dir}",
            f"{t('diag.hotkey_spec'):<14} {s.get('hotkeys')}",
        ]
        if s.get("warning"):
            lines.append(f"{t('diag.warning'):<14} " + s["warning"])
        self.v_diag.set("\n".join(lines))

    def _probe(self) -> None:
        self.probe_out.delete("1.0", "end")
        self.probe_out.insert("end", t("probe.starting"))

        def job():
            try:
                frame = self.ctx.screen.grab()
                found, scores = self.ctx.store.probe(frame)
                money = self.ctx.reader.read(frame)
                lines = [t("probe.frame", w=frame.size[0], h=frame.size[1],
                           scale=self.ctx.store.frame_scale(frame))]
                for name in self.ctx.store.templates:
                    m = next((x for x in found if x.name == name), None)
                    lines.append(f"  {name:<14} {scores.get(name, 0):.3f}  "
                                 f"thr={self.ctx.store.templates[name].threshold}"
                                 + (f"  @({m.x},{m.y})" if m else f"  {t('probe.miss')}"))
                lines.append(t("probe.money", value=money.value, raw=money.raw,
                               backend=money.backend))
                event("GUI PROBE", action="probe", size=list(frame.size),
                      scores={k: round(float(v), 3) for k, v in scores.items()},
                      money=money.value, backend=money.backend)
                text = "\n".join(lines) + "\n"
            except Exception as exc:
                text = t("probe.done", error=exc)
            self.root.after(0, lambda: self.probe_out.insert("end", text))

        threading.Thread(target=job, daemon=True, name="gui-probe").start()

    # --------------------------------------------------------------- open --

    @staticmethod
    def _open(path: str) -> None:
        try:
            os.startfile(path)
        except Exception as exc:
            messagebox.showerror(t("err.cannot_open"), f"{path}\n{exc}")

    def _open_config(self) -> None:
        self._open(self.ctx.config.path)

    def _open_log(self) -> None:
        self._open(os.path.join(self.ctx.log_dir, "bot.log"))

    def _open_debug(self) -> None:
        self._open(os.path.join(self.ctx.log_dir, "debug"))

    # ---------------------------------------------------------------- tick --

    def _tick(self) -> None:
        s = self.ctx.runner.status()
        if s["running"]:
            self.v_state.set(t("state.running") + " · " + phase_text(s.get("phase")))
        else:
            self.v_state.set(t("state.idle"))
        for key, var in self.cards.items():
            var.set(_fmt_value(s.get(key)))
        running = bool(s["running"])
        self.btn_start.configure(state="disabled" if running else "normal")
        for b in (self.btn_stop, self.btn_panic, self.btn_toggle):
            b.configure(state="normal" if running else "disabled")
        self.v_admin.set(t("admin.yes") if self._snapshot.get("elevated")
                         else t("admin.no"))
        if time.time() - self._last_snapshot > 2.0:
            self._snapshot_now()
        items, self.last_log_id = BUS.since(self.last_log_id, 200)
        for item in items:
            self._append_log(item)
        self.root.after(250, self._tick)


def run_gui(ctx, open_settings: bool = False) -> int:
    root = tk.Tk()
    app = App(root, ctx, open_settings=open_settings)
    root.mainloop()
    return 0
