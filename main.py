"""HSR currency-war S2 auto money printer - entry point.

Native GUI is the default and is what the distributed build runs:

    AutoMoneyPrinter.exe                 # desktop console (asks for UAC)
    python main.py                       # same, from source
    python main.py --settings            # opens the GUI on the Settings tab
    python main.py --language en         # force English (or zh) at startup
    python main.py --run --dry-run       # run once in the terminal, no clicking
    python main.py --probe screenshot/x.png
    python main.py --web                 # legacy browser console (source only)

Every interactive mode re-launches itself elevated, because the game runs as
administrator and Windows UIPI drops synthetic input from a lower-integrity
process. Add --no-elevate to skip that.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

from app.config import Config
from app.context import Context
from app.logbus import LOG, event, setup_logging
from app.runtime import elevate, resolve_paths
from app.vision import ImageScreen, enable_dpi_awareness


def build(config_path: str | None = None) -> tuple[Config, Context, str]:
    enable_dpi_awareness()
    rt = resolve_paths(config_path)
    cfg = Config(rt.config_path)
    cfg.set_overrides(rt.overrides)
    log_dir = os.path.join(rt.root, cfg.get()["log_dir"])
    path = setup_logging(log_dir)
    ctx = Context(cfg, log_dir, rt.root)
    ctx.runtime = rt
    return cfg, ctx, path


def cli_probe(ctx: Context, pattern: str) -> int:
    paths = sorted(glob.glob(pattern)) or ([pattern] if os.path.isfile(pattern) else [])
    if not paths:
        LOG.error("no screenshot matched %s", pattern)
        return 2
    for p in paths:
        frame = ImageScreen(p).grab()
        found, scores = ctx.store.probe(frame)
        money = ctx.reader.read(frame)
        print(f"\n=== {os.path.basename(p)} {frame.size}")
        for name in ctx.store.templates:
            th = ctx.store.templates[name].threshold
            flag = "HIT" if any(m.name == name for m in found) else "   "
            print(f"  {flag} {name:<14} score={scores.get(name, 0):.3f}  threshold={th}")
        print(f"  money: value={money.value} raw={money.raw!r} backend={money.backend} box={money.box}")
        for m in found:
            print(f"    -> {m.name} anchor=({m.x},{m.y}) box={m.box} score={m.score:.3f}")
    return 0


def cli_run(ctx: Context, dry_run: bool, cycles: int) -> int:
    ctx.runner.start(dry_run=dry_run, cycles=cycles, trigger="cli")
    thread = ctx.runner._thread
    last_phase = ""
    try:
        while thread and thread.is_alive():
            if ctx.runner.s.phase != last_phase:
                last_phase = ctx.runner.s.phase
                print(f"  phase: {last_phase}", flush=True)
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nCtrl-C -> stopping", flush=True)
        ctx.runner.stop()
        if thread:
            thread.join(timeout=5)
    s = ctx.runner.s
    print("\n--- summary ---")
    print(f"  {s.message}")
    print(f"  money={s.money} buys={s.buys} refreshes={s.refreshes} "
          f"drags={s.drags} sold={s.sold} profit={s.profit}")
    if s.last_error:
        print(f"  error: {s.last_error}")
    return 0 if s.message == "completed" else 1


def _legacy_web(ctx: Context, host: str, port: int, settings: bool) -> int:
    import threading
    import webbrowser
    from app.web import create_app
    url = f"http://{host}:{port}/"
    event("SERVER READY", action="server", url=url, log=ctx.log_dir)
    print(f"\nLegacy web console: {url}")
    if not settings:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    else:
        threading.Timer(0.8, lambda: webbrowser.open(url + "#settings")).start()
    try:
        create_app(ctx).run(host=host, port=port, threaded=True, use_reloader=False)
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="HSR S2 auto money printer")
    ap.add_argument("--config", default=None)
    ap.add_argument("--language", choices=("en", "zh"), default=None,
                    help="GUI language (en / zh), persisted to config.json")
    ap.add_argument("--gui", action="store_true", help="native GUI (the default)")
    ap.add_argument("--settings", action="store_true",
                    help="open the GUI on the Settings tab")
    ap.add_argument("--web", action="store_true",
                    help="legacy browser console (source tree only)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-hotkeys", action="store_true")
    ap.add_argument("--no-elevate", action="store_true",
                    help="do not ask for administrator rights")
    ap.add_argument("--run", action="store_true", help="run once without the GUI")
    ap.add_argument("--dry-run", action="store_true", help="log actions but never move the mouse")
    ap.add_argument("--real", action="store_true", help="allow real mouse input")
    ap.add_argument("--cycles", type=int, default=None)
    ap.add_argument("--probe", metavar="GLOB", help="offline template check against screenshots")
    args = ap.parse_args()

    # Elevate before anything binds a port or opens a window; this process exits
    # once the elevated copy starts.
    if not args.probe and elevate(sys.argv[1:]):
        return 0

    cfg, ctx, log_path = build(args.config)
    if args.language:
        cfg.update({"language": args.language})

    try:
        from app.window import is_elevated, window_elevated
        title = ((cfg.get().get("behavior") or {}).get("start") or {}).get("activate_window")
        if title and window_elevated(title) and not is_elevated():
            LOG.warning("the game window runs as administrator but this app does not: "
                        "synthetic clicks will be discarded. Restart and accept the UAC prompt.")
    except Exception:
        pass

    try:
        if args.probe:
            return cli_probe(ctx, args.probe)

        if args.run:
            dry = True if args.dry_run else (False if args.real else cfg.get()["dry_run"])
            cycles = args.cycles if args.cycles is not None \
                else int(cfg.get()["economy"].get("cycles", 0))
            return cli_run(ctx, dry, cycles)

        if args.web:
            if not args.no_hotkeys:
                ctx.boot()
            return _legacy_web(ctx, args.host, args.port, args.settings)

        if not args.no_hotkeys:
            ctx.boot()
        event("GUI READY", action="gui", config=ctx.config.path, log=log_path,
              hotkeys=cfg.get()["hotkeys"])
        hk = cfg.get()["hotkeys"]
        print("\n" + "=" * 58)
        print("  Auto Money Printer  ·  HSR 货币战争 S2")
        for name in ("start", "stop", "panic", "toggle"):
            if hk.get(name):
                print(f"    {hk[name]:<12} {name}")
        print(f"  dry_run = {cfg.get()['dry_run']}")
        print("=" * 58 + "\n", flush=True)
        from app.gui import run_gui
        return run_gui(ctx, open_settings=args.settings)
    except KeyboardInterrupt:
        return 0
    finally:
        ctx.runner.stop()
        ctx.shutdown()


if __name__ == "__main__":
    sys.exit(main())
