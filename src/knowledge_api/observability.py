import time
from collections.abc import Callable

from prometheus_client import Counter, Histogram, generate_latest

REQUESTS = Counter(
    "knowledge_api_requests_total",
    "HTTP requests handled",
    ["method", "path", "status"],
)
REQUEST_LATENCY = Histogram(
    "knowledge_api_request_duration_seconds",
    "HTTP request latency",
    ["method", "path"],
)
PROVIDER_CALLS = Counter(
    "knowledge_api_provider_calls_total", "Provider calls", ["provider", "operation"]
)


def metrics_payload() -> bytes:
    return generate_latest()


async def observe_request(request: object, call_next: Callable[[object], object]) -> object:
    """Kept as a small standalone seam for instrumentation tests."""
    started = time.perf_counter()
    response = await call_next(request)  # type: ignore[misc]
    del started
    return response
