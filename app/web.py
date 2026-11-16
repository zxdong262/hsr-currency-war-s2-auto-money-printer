"""Local HTTP API + single-page HTML test console."""
from __future__ import annotations

import os

from flask import Flask, jsonify, request, send_file

from .bot import Bot, State
from .context import Context  # noqa: F401 - re-exported for older imports
from .display import describe as describe_display
from .logbus import BUS, event
from .vision import ImageScreen, png_data_url
from .window import activate_window, find_window, foreground_title


def create_app(ctx: Context) -> Flask:
    app = Flask(__name__, static_folder=None)
    web_dir = os.path.join(ctx.root, "web")

    @app.get("/")
    def index():
        return send_file(os.path.join(web_dir, "index.html"))

    @app.get("/api/status")
    def status():
        return jsonify(ctx.snapshot())

    @app.post("/api/activate")
    def activate():
        start_cfg = (ctx.config.get()["behavior"] or {}).get("start") or {}
        title = start_cfg.get("activate_window")
        if not title:
            return jsonify({"ok": False, "error": "behavior.start.activate_window is not set"}), 400
        ok, why = activate_window(title)
        event("WINDOW FOCUS", action="focus", title=title, ok=ok, result=why,
              foreground_before=foreground_title(), level=20 if ok else 30)
        win = find_window(title)
        return jsonify({"ok": ok, "result": why,
                        "title": win["title"] if win else None,
                        "rect": list(win["rect"]) if win else None,
                        "foreground": foreground_title()})

    @app.get("/api/config")
    def get_config():
        return jsonify(ctx.config.get())

    @app.put("/api/config")
    def put_config():
        data = request.get_json(force=True, silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "expected a JSON object"}), 400
        try:
            return jsonify(ctx.apply_config(data))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/config")
    def patch_config():
        data = request.get_json(force=True, silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "expected a JSON object"}), 400
        try:
            return jsonify(ctx.apply_config(data))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @app.get("/api/display")
    def display_info():
        return jsonify(describe_display(ctx.config.get()))

    @app.post("/api/display")
    def display_change():
        data = request.get_json(force=True, silent=True) or {}
        try:
            width, height = int(data["width"]), int(data["height"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "width and height must be integers"}), 400
        result = ctx.change_resolution(width, height, bool(data.get("confirmed")))
        if result.get("need_confirm"):
            return jsonify(result)
        return jsonify(result), (200 if result.get("ok") else 400)

    @app.get("/api/logs")
    def logs():
        try:
            since = int(request.args.get("since", "0"))
        except ValueError:
            since = 0
        items, seq = BUS.since(since, 400)
        return jsonify({"items": items, "last_id": seq})

    @app.get("/api/logfile")
    def logfile():
        path = os.path.join(ctx.log_dir, "bot.log")
        return send_file(path, as_attachment=True, download_name="bot.log") \
            if os.path.isfile(path) else (jsonify({"error": "no log yet"}), 404)

    @app.post("/api/start")
    def start():
        data = request.get_json(force=True, silent=True) or {}
        ok = ctx.runner.start(dry_run=data.get("dry_run"), cycles=data.get("cycles"),
                              trigger="web")
        return jsonify({"started": ok, "already_running": not ok})

    @app.post("/api/stop")
    def stop():
        ctx.runner.stop()
        return jsonify({"ok": True})

    @app.post("/api/panic")
    def panic():
        ctx.runner.panic()
        return jsonify({"ok": True})

    @app.post("/api/money")
    def set_money():
        data = request.get_json(force=True, silent=True) or {}
        try:
            value = int(data["value"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "value must be an integer"}), 400
        ctx.runner.s.money = value
        ctx.runner.s.money_source = "manual"
        event("MONEY SET", action="money", value=value, source="manual")
        return jsonify({"money": value})

    @app.get("/api/screenshots")
    def screenshots():
        return jsonify({"files": ctx.screenshot_files()})

    @app.get("/api/templates")
    def templates():
        out = {}
        for name, tpl in ctx.store.templates.items():
            out[name] = {
                "file": ctx.config.get()["templates"][name]["file"],
                "size": list(tpl.full_size),
                "crop_size": [tpl.image.shape[1], tpl.image.shape[0]],
                "anchor": list(tpl.anchor),
                "threshold": tpl.threshold,
                "roi": tpl.roi,
                "thumb": png_data_url(tpl.image, 320),
            }
        return jsonify(out)

    @app.get("/api/probe")
    def probe():
        source = request.args.get("source", "live")
        try:
            th = float(request.args.get("threshold", "")) if request.args.get("threshold") else None
        except ValueError:
            th = None
        try:
            frame = ctx.frame_for(source)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        found, scores = ctx.store.probe(frame, th)
        money = ctx.reader.read(frame)
        annotated = ctx.store.annotate(frame, found)

        # Visualise the sell-zone boundary so a middle-screen false positive is obvious.
        cfg = ctx.config.get()
        zone = cfg["behavior"].get("sell_zone") or {}
        frac = min(0.95, max(0.0, float(zone.get("bottom_fraction", 0.20))))
        min_y = int(frame.origin[1] + frame.image.shape[0] * (1.0 - frac))
        y = min_y - frame.origin[1]
        if zone.get("enable", True) and 0 <= y < annotated.shape[0]:
            import cv2
            cv2.line(annotated, (0, y), (annotated.shape[1], y), (40, 40, 235), 2, cv2.LINE_AA)
            cv2.putText(annotated, "SELL ZONE - only cards BELOW this line may be sold",
                        (12, max(18, y - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (40, 40, 235), 2, cv2.LINE_AA)

        bot = Bot(ctx.config, ctx.screen, ctx.store, ctx.reader, None, State(), cfg, 1)
        cards = bot.bench_cards(frame)
        card_rows = [
            {"x": m.x, "y": m.y, "score": round(m.score, 3),
             "in_zone": m.y >= min_y}
            for m in sorted(cards, key=lambda m: m.x)
        ]

        return jsonify({
            "source": frame.source,
            "size": list(frame.size),
            "origin": list(frame.origin),
            "matches": [m.as_dict() for m in found],
            "scores": scores,
            "cards": card_rows,
            "sell_zone": {"min_y": min_y, "bottom_fraction": frac,
                          "enabled": bool(zone.get("enable", True))},
            "image": png_data_url(annotated, 1600),
            "money": {
                "value": money.value, "raw": money.raw, "backend": money.backend,
                "box": money.box,
                "crop": png_data_url(money.crop, 640) if money.crop is not None else "",
            },
        })

    @app.get("/api/screen")
    def screen():
        source = request.args.get("source", "live")
        try:
            frame = ctx.frame_for(source)
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"image": png_data_url(frame.image, 1600), "source": frame.source})

    @app.get("/api/debug/<name>")
    def debug_image(name):
        path = os.path.join(ctx.log_dir, "debug", os.path.basename(name))
        return send_file(path) if os.path.isfile(path) else ("", 404)

    return app
