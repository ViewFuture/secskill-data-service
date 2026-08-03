"""进程内线程安全 TTL 缓存。"""

from __future__ import annotations

import threading
import time
from typing import Any


class TtlCache:
    """简单进程内 TTL 缓存（线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at < now:
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        expires_at = time.monotonic() + float(ttl_seconds)
        with self._lock:
            self._store[key] = (expires_at, value)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
