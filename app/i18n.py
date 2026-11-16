"""Minimal process-wide English / Simplified Chinese strings for the native GUI.

Only user-facing GUI text lives here. Bot events, logs and template names stay in
their original English form so log files remain stable across languages.
"""
from __future__ import annotations

import locale
import os
import re

SUPPORTED = ("en", "zh")
_LANG = "en"

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "yes": "yes",
        "no": "no",
        "phase.idle": "idle",
        "phase.starting": "starting",
        "phase.task1_open_market": "task1 / open market",
        "phase.task1_buy_loop": "task1 / buy loop",
        "phase.task2_duplicate": "task2 / duplicate",
        "phase.task2_sell": "task2 / sell",
        "language": "Language",
        "lang.en": "English",
        "lang.zh": "中文",

        "window.title": "Auto Money Printer  -  HSR S2",
        "state.idle": "IDLE",
        "state.running": "RUNNING",
        "admin.yes": "administrator",
        "admin.no": "not elevated",
        "note.already_running": "already running",
        "note.stop_requested": "stop requested",
        "note.panic_stop": "panic stop",
        "note.focus": "focus: {why}",

        "tab.control": "Control",
        "tab.settings": "Settings",
        "tab.log": "Log",
        "tab.diagnostics": "Diagnostics",

        "card.phase": "Phase",
        "card.money": "Money",
        "card.buys": "Buys",
        "card.refreshes": "Refreshes",
        "card.drags": "Drags",
        "card.sold": "Sold",
        "card.profit": "Profit",
        "card.elapsed": "Elapsed (s)",

        "btn.start": "Start",
        "btn.stop": "Stop",
        "btn.panic": "Panic",
        "btn.toggle": "Toggle",
        "btn.focus_game": "Focus game",
        "opt.dry_run": "Dry run (log only, no clicks)",
        "hint.hotkeys_global": "Hotkeys are global; edit them in Settings.",
        "label.recent_events": "Recent events",

        "box.shortcuts": "Global shortcuts",
        "hk.start": "Start",
        "hk.stop": "Stop",
        "hk.panic": "Panic",
        "hk.toggle": "Toggle",
        "hk.start.tip": "Start the bot while the game has focus",
        "hk.stop.tip": "Stop after the current action",
        "hk.panic.tip": "Immediate stop + release the mouse button",
        "hk.toggle.tip": "Start / stop with one key",
        "btn.set_shortcut": "Set...",
        "hint.shortcut_format": "Format: <f6> or <ctrl>+<alt>+s. Blank disables.",
        "capture.title": "Set shortcut",
        "capture.press": "Press the key combination now.",
        "capture.none": "(none)",
        "capture.hint": "Enter = confirm   ·   Esc = cancel",

        "box.display": "Display",
        "display.line": "Screen {w}x{h}  ·  scale {scale}  ·  {mode}",
        "display.supported": "supported",
        "display.custom": "custom",
        "hint.resolution_confirm": "Changing the resolution asks for confirmation first.",

        "box.values": "Values",
        "val.pack_price": "Pack price",
        "val.card_sell_price": "Card sell value",
        "val.refresh_cost": "Refresh cost",
        "val.drag_batch_max": "Drags before selling",
        "val.max_refreshes": "Max refreshes (0 = unlimited)",
        "val.max_sells": "Max sells",
        "val.max_buys": "Max buys",
        "val.max_drag_attempts": "Max drag attempts",
        "val.cycles": "Cycles (0 = until stopped)",
        "val.bottom_fraction": "Sell zone height (0-1)",
        "val.stop_after_idle_refreshes": "Stop after idle refreshes",
        "val.debug_images": "Debug screenshots",
        "btn.save_settings": "Save settings",
        "btn.reload_file": "Reload from file",
        "btn.open_config": "Open config.json",
        "settings.saved": "saved",
        "settings.unsaved": "unsaved changes",

        "log.level": "Level",
        "btn.open_log": "Open bot.log",
        "btn.open_debug": "Open debug folder",
        "btn.open_log_folder": "Open log folder",

        "diag.state": "state",
        "diag.screen": "screen",
        "diag.capture": "capture",
        "diag.admin": "admin",
        "diag.game_admin": "game admin",
        "diag.game_window": "game window",
        "diag.hotkeys": "hotkeys",
        "diag.backends": "backends",
        "diag.config": "config",
        "diag.templates": "templates",
        "diag.hotkey_spec": "hotkey spec",
        "diag.running": "running",
        "diag.idle": "idle",
        "diag.warning": "WARNING",
        "diag.failed": "diagnostics failed: {error}",
        "btn.probe": "Probe screen",
        "btn.refresh": "Refresh",
        "probe.starting": "probing the live screen...\n",
        "probe.frame": "frame {w}x{h}  scale {scale}",
        "probe.miss": "MISS",
        "probe.money": "money          {value}  raw={raw!r}  {backend}",
        "probe.done": "probe failed: {error}\n",

        "err.invalid_value": "Invalid value",
        "err.sell_zone": "Sell zone height must be between 0 and 0.95",
        "err.invalid_shortcut": "Invalid shortcut",
        "err.shortcut_detail": "Cannot use {spec!r} for {key}.\nUse a form like <f6> or <ctrl>+<alt>+s.",
        "err.save_failed": "Save failed",
        "dlg.change_resolution": "Change screen resolution",
        "dlg.change_resolution_msg": "Switch the screen from {current} to {width} x {height}?\n\nThe game may need a moment to resize.",
        "err.resolution_failed": "Resolution change failed",
        "err.unknown": "unknown error",
        "dlg.quit": "Quit",
        "dlg.quit_msg": "The bot is still running. Stop it and quit?",
        "err.cannot_open": "Cannot open",
    },
    "zh": {
        "yes": "是",
        "no": "否",
        "phase.idle": "空闲",
        "phase.starting": "启动中",
        "phase.task1_open_market": "任务1 / 打开商店",
        "phase.task1_buy_loop": "任务1 / 购买",
        "phase.task2_duplicate": "任务2 / 复制",
        "phase.task2_sell": "任务2 / 出售",
        "language": "语言",
        "lang.en": "English",
        "lang.zh": "中文",

        "window.title": "自动刷钱器  -  崩铁 S2",
        "state.idle": "空闲",
        "state.running": "运行中",
        "admin.yes": "已提权",
        "admin.no": "未提权",
        "note.already_running": "已在运行",
        "note.stop_requested": "已请求停止",
        "note.panic_stop": "已急停",
        "note.focus": "聚焦：{why}",

        "tab.control": "控制",
        "tab.settings": "设置",
        "tab.log": "日志",
        "tab.diagnostics": "诊断",

        "card.phase": "阶段",
        "card.money": "金币",
        "card.buys": "购买",
        "card.refreshes": "刷新",
        "card.drags": "拖拽",
        "card.sold": "出售",
        "card.profit": "收益",
        "card.elapsed": "耗时(秒)",

        "btn.start": "开始",
        "btn.stop": "停止",
        "btn.panic": "急停",
        "btn.toggle": "切换",
        "btn.focus_game": "聚焦游戏",
        "opt.dry_run": "试运行（只记录，不点击）",
        "hint.hotkeys_global": "快捷键为全局生效，在设置中修改。",
        "label.recent_events": "最近事件",

        "box.shortcuts": "全局快捷键",
        "hk.start": "开始",
        "hk.stop": "停止",
        "hk.panic": "急停",
        "hk.toggle": "切换",
        "hk.start.tip": "游戏在前台时启动脚本",
        "hk.stop.tip": "当前动作结束后停止",
        "hk.panic.tip": "立即停止并松开鼠标",
        "hk.toggle.tip": "一键开始 / 停止",
        "btn.set_shortcut": "设置...",
        "hint.shortcut_format": "格式：<f6> 或 <ctrl>+<alt>+s，留空表示禁用。",
        "capture.title": "设置快捷键",
        "capture.press": "请按下组合键。",
        "capture.none": "（无）",
        "capture.hint": "回车 = 确认   ·   Esc = 取消",

        "box.display": "显示",
        "display.line": "屏幕 {w}×{h}  ·  缩放 {scale}  ·  {mode}",
        "display.supported": "支持",
        "display.custom": "自定义",
        "hint.resolution_confirm": "切换分辨率前会先请求确认。",

        "box.values": "数值",
        "val.pack_price": "卡包价格",
        "val.card_sell_price": "卡牌售价",
        "val.refresh_cost": "刷新花费",
        "val.drag_batch_max": "出售前拖拽次数",
        "val.max_refreshes": "最大刷新次数（0 = 不限）",
        "val.max_sells": "最大出售数",
        "val.max_buys": "最大购买数",
        "val.max_drag_attempts": "最大拖拽尝试",
        "val.cycles": "轮数（0 = 直到手动停止）",
        "val.bottom_fraction": "出售区高度（0-1）",
        "val.stop_after_idle_refreshes": "空刷多少次后停止",
        "val.debug_images": "保存调试截图",
        "btn.save_settings": "保存设置",
        "btn.reload_file": "从文件重载",
        "btn.open_config": "打开 config.json",
        "settings.saved": "已保存",
        "settings.unsaved": "有未保存的修改",

        "log.level": "级别",
        "btn.open_log": "打开 bot.log",
        "btn.open_debug": "打开调试目录",
        "btn.open_log_folder": "打开日志目录",

        "diag.state": "状态",
        "diag.screen": "屏幕",
        "diag.capture": "抓屏",
        "diag.admin": "管理员",
        "diag.game_admin": "游戏管理员",
        "diag.game_window": "游戏窗口",
        "diag.hotkeys": "快捷键",
        "diag.backends": "后端",
        "diag.config": "配置",
        "diag.templates": "模板",
        "diag.hotkey_spec": "快捷键配置",
        "diag.running": "运行中",
        "diag.idle": "空闲",
        "diag.warning": "警告",
        "diag.failed": "诊断失败：{error}",
        "btn.probe": "探测屏幕",
        "btn.refresh": "刷新",
        "probe.starting": "正在探测当前屏幕...\n",
        "probe.frame": "画面 {w}×{h}  缩放 {scale}",
        "probe.miss": "未命中",
        "probe.money": "金币          {value}  原始={raw!r}  {backend}",
        "probe.done": "探测失败：{error}\n",

        "err.invalid_value": "数值无效",
        "err.sell_zone": "出售区高度必须在 0 到 0.95 之间",
        "err.invalid_shortcut": "快捷键无效",
        "err.shortcut_detail": "{key} 不能使用 {spec!r}。\n请使用 <f6> 或 <ctrl>+<alt>+s 这样的格式。",
        "err.save_failed": "保存失败",
        "dlg.change_resolution": "切换分辨率",
        "dlg.change_resolution_msg": "将屏幕从 {current} 切换到 {width} × {height}？\n\n游戏可能需要一点时间适应新分辨率。",
        "err.resolution_failed": "分辨率切换失败",
        "err.unknown": "未知错误",
        "dlg.quit": "退出",
        "dlg.quit_msg": "脚本仍在运行，确定停止并退出吗？",
        "err.cannot_open": "无法打开",
    },
}


def normalize_language(value: str | None) -> str:
    value = (value or "").strip().lower().replace("_", "-")
    if value.startswith("zh") or value.startswith("cn"):
        return "zh"
    if value.startswith("en"):
        return "en"
    return ""


def detect_language() -> str:
    """Best-effort locale detection, used only when config has no language key."""
    raw = ""
    for getter in (lambda: locale.getlocale()[0], locale.getdefaultlocale,
                   lambda: os.environ.get("LANG") or os.environ.get("LC_ALL")):
        try:
            raw = getter() or ""
        except Exception:
            raw = ""
        found = normalize_language(str(raw))
        if found:
            return found
    return "en"


def set_language(value: str | None) -> str:
    global _LANG
    _LANG = normalize_language(value) or "en"
    return _LANG


def language() -> str:
    return _LANG


def t(key: str, **kwargs) -> str:
    text = STRINGS.get(_LANG, {}).get(key, STRINGS["en"].get(key, key))
    return text.format(**kwargs) if kwargs else text


def phase_text(value) -> str:
    """Translate the runner's English phase names, keeping unknown ones intact."""
    key = "phase." + re.sub(r"[^a-z0-9]+", "_",
                            str(value or "idle").lower()).strip("_")
    text = STRINGS.get(_LANG, {}).get(key) or STRINGS["en"].get(key)
    return text if text is not None else str(value or "-")
