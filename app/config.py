"""JSON config with deep-merge updates; safe to edit while the bot is idle."""
from __future__ import annotations

import copy
import json
import os
import threading

DEFAULT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


class Config:
    def __init__(self, path: str = DEFAULT_FILE):
        self.path = path
        self._lock = threading.RLock()
        self._overrides: dict = {}
        with open(path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

    def set_overrides(self, patch: dict) -> None:
        """In-memory values for packaged builds; never written back to disk."""
        with self._lock:
            self._overrides = copy.deepcopy(patch or {})

    def get(self) -> dict:
        with self._lock:
            return _deep_merge(self._data, self._overrides)

    def update(self, patch: dict, persist: bool = True) -> dict:
        with self._lock:
            self._data = _deep_merge(self._data, patch)
            if persist:
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            return self.get()

    def replace(self, data: dict) -> dict:
        with self._lock:
            self._data = copy.deepcopy(data)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            return self.get()

    @property
    def root(self) -> str:
        return os.path.dirname(os.path.abspath(self.path))

    def abs_path(self, value: str) -> str:
        return value if os.path.isabs(value) else os.path.join(self.root, value)
