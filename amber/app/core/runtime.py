"""Lightweight runtime guards for overload and graceful shutdown."""
from __future__ import annotations

import asyncio


class RuntimeGuard:
    def __init__(self, max_concurrent_requests: int) -> None:
        self._max = max_concurrent_requests
        self._active = 0
        self._lock = asyncio.Lock()
        self._shutting_down = False

    async def try_acquire(self) -> bool:
        async with self._lock:
            if self._shutting_down or self._active >= self._max:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            if self._active > 0:
                self._active -= 1

    async def mark_shutting_down(self) -> None:
        async with self._lock:
            self._shutting_down = True

    def snapshot(self) -> dict[str, int | bool]:
        return {
            "max_concurrent_requests": self._max,
            "active_requests": self._active,
            "shutting_down": self._shutting_down,
        }
