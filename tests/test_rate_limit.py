import pytest

from knowledge_api.rate_limit import InMemoryRateLimiter


@pytest.mark.asyncio
async def test_in_memory_rate_limiter_window_hook() -> None:
    limiter = InMemoryRateLimiter()
    assert await limiter.check("tenant:user", 1, window_seconds=60) == (True, 0)
    allowed, retry_after = await limiter.check("tenant:user", 1, window_seconds=60)
    assert allowed is False
    assert retry_after >= 1
    assert await limiter.check("other:user", 1, window_seconds=60) == (True, 0)
