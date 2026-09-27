from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import __version__
from .api import audit, auth, conversations, documents, health, knowledge, workspaces
from .config import Settings, get_settings
from .db import create_engine, init_db
from .jobs import InlineJobQueue, RedisJobQueue
from .observability import REQUEST_LATENCY, REQUESTS, metrics_payload
from .providers import (
    AnthropicChatProvider,
    DeterministicEmbeddingProvider,
    DeterministicLLMProvider,
    EmbeddingProvider,
    LLMProvider,
    OpenAIChatProvider,
    OpenAIEmbeddingProvider,
)
from .rate_limit import InMemoryRateLimiter, RedisRateLimiter
from .storage import LocalObjectStorage, ObjectStorage, S3CompatibleStorage


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider.lower() == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for the OpenAI embedding provider")
        return OpenAIEmbeddingProvider(settings.openai_api_key, settings.openai_embedding_model)
    return DeterministicEmbeddingProvider()


def build_llm_provider(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider.lower()
    if provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for the OpenAI chat provider")
        return OpenAIChatProvider(settings.openai_api_key, settings.openai_chat_model)
    if provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for the Anthropic provider")
        return AnthropicChatProvider(settings.anthropic_api_key, settings.anthropic_model)
    return DeterministicLLMProvider()


def build_storage(settings: Settings) -> ObjectStorage:
    if settings.storage_backend.lower() == "s3":
        if not settings.s3_bucket:
            raise ValueError("S3_BUCKET is required for S3 storage")
        return S3CompatibleStorage(settings.s3_bucket, settings.s3_region, settings.s3_endpoint_url)
    return LocalObjectStorage(settings.storage_root)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if app.state.settings.environment.lower() not in {"production", "prod"}:
        await init_db(app.state.engine)
    yield
    await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    database_url = settings.database_url
    if database_url.startswith("postgresql://"):
        database_url = "postgresql+asyncpg://" + database_url.removeprefix("postgresql://")
    engine = create_engine(database_url, echo=settings.debug)
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Tenant-isolated AI knowledge SaaS modular monolith",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    app.state.storage = build_storage(settings)
    app.state.embedding_provider = build_embedding_provider(settings)
    app.state.llm_provider = build_llm_provider(settings)
    app.state.rate_limiter = (
        RedisRateLimiter(settings.redis_url) if settings.redis_url else InMemoryRateLimiter()
    )
    app.state.queue = (
        RedisJobQueue(settings.redis_url)
        if settings.queue_backend == "redis" and settings.redis_url
        else InlineJobQueue()
    )

    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-API-Key",
                "X-Dev-User-Id",
                "Idempotency-Key",
            ],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-Id", "")[:100] or str(uuid4())
        request.state.request_id = request_id
        started = perf_counter()
        response = await call_next(request)
        path = request.url.path
        status_code = str(response.status_code)
        REQUESTS.labels(request.method, path, status_code).inc()
        REQUEST_LATENCY.labels(request.method, path).observe(perf_counter() - started)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {"service": settings.app_name, "version": __version__}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(content=metrics_payload(), media_type=CONTENT_TYPE_LATEST)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(workspaces.router)
    app.include_router(documents.router)
    app.include_router(knowledge.router)
    app.include_router(conversations.router)
    app.include_router(audit.router)
    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("knowledge_api.main:app", host="0.0.0.0", port=8000, reload=False)
