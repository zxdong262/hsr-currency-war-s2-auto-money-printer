"""Structured logging: rotating file on disk + in-memory bus for the web UI."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from logging.handlers import RotatingFileHandler

LOG = logging.getLogger("bot")


class Bus(logging.Handler):
    """Small ring buffer of structured records, pollable by the HTML interface."""

    def __init__(self, capacity: int = 3000):
        super().__init__(level=logging.DEBUG)
        self._records: deque[dict] = deque(maxlen=capacity)
        self._seq = 0
        self._cond = threading.Condition(threading.Lock())
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        data = getattr(record, "data", None) or {}
        item = {
            "id": 0,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created)),
            "ms": int((record.created % 1) * 1000),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "data": data,
        }
        with self._cond:
            self._seq += 1
            item["id"] = self._seq
            self._records.append(item)
            self._cond.notify_all()

    def since(self, last_id: int, limit: int = 300) -> tuple[list[dict], int]:
        with self._cond:
            items = [r for r in self._records if r["id"] > last_id][-limit:]
            return items, self._seq

    @property
    def last_id(self) -> int:
        with self._cond:
            return self._seq

    def snapshot(self, limit: int = 300) -> list[dict]:
        with self._cond:
            return list(self._records)[-limit:]


BUS = Bus()


class FileFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{self.formatTime(record, '%Y-%m-%d %H:%M:%S')}."
            f"{int(record.msecs):03d} {record.levelname:<7} [{record.name}] "
            f"{record.getMessage()}"
        )
        data = getattr(record, "data", None)
        if data:
            base += "  " + json.dumps(data, ensure_ascii=False, default=str)
        return base


def setup_logging(log_dir: str, level: int = logging.DEBUG) -> str:
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "bot.log")
    root = logging.getLogger("bot")
    root.setLevel(level)
    root.propagate = False
    for h in list(root.handlers):
        root.removeHandler(h)

    fh = RotatingFileHandler(path, maxBytes=4 * 1024 * 1024, backupCount=4, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(FileFormatter())
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    root.addHandler(ch)

    if BUS not in root.handlers:
        root.addHandler(BUS)
    return path


def event(msg: str, level: int = logging.INFO, **data) -> None:
    """Log an action with machine-readable context attached."""
    LOG.log(level, msg, extra={"data": data})
