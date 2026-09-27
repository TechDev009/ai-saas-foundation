"""Background job boundary; inline jobs keep the local flow deterministic."""

from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

T = TypeVar("T")


class JobQueue(Protocol):
    async def enqueue(self, job: Callable[[], Awaitable[T]]) -> T | None: ...


class InlineJobQueue:
    async def enqueue(self, job: Callable[[], Awaitable[T]]) -> T:
        return await job()


class RedisJobQueue:
    """Integration seam for a worker system (RQ, Dramatiq, Celery, or cloud queue).

    The modular monolith keeps job contracts here; deploying workers is optional.
    """

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url

    async def enqueue(self, job: Callable[[], Awaitable[T]]) -> None:
        del job
        raise RuntimeError("RedisJobQueue needs a configured worker implementation")
