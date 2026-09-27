"""Rate-limit/cache abstraction. Redis can replace the in-memory implementation."""

import asyncio
from collections import defaultdict, deque
from time import monotonic
from typing import Protocol


class RateLimiter(Protocol):
    async def check(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]: ...


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._events: defaultdict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        now = monotonic()
        async with self._lock:
            events = self._events[key]
            cutoff = now - window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(events[0] + window_seconds - now))
                return False, retry_after
            events.append(now)
            return True, 0


class RedisRateLimiter:
    """Atomic Redis fixed-window hook; use a managed Redis in production."""

    def __init__(self, redis_url: str) -> None:
        from redis.asyncio import Redis

        self.redis = Redis.from_url(redis_url, decode_responses=True)

    async def check(self, key: str, limit: int, window_seconds: int = 60) -> tuple[bool, int]:
        bucket = f"knowledge:rate:{key}:{int(monotonic() // window_seconds)}"
        count = await self.redis.incr(bucket)
        if count == 1:
            await self.redis.expire(bucket, window_seconds)
        if count > limit:
            return False, window_seconds
        return True, 0


class Cache(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


class NoopCache:
    async def get(self, key: str) -> str | None:
        del key
        return None

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        del key, value, ttl_seconds
