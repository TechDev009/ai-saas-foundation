from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pytest_asyncio
from fastapi import FastAPI

from knowledge_api.config import Settings
from knowledge_api.db import init_db
from knowledge_api.main import create_app


@pytest_asyncio.fixture
async def app() -> AsyncIterator[FastAPI]:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        settings = Settings(
            environment="test",
            allow_dev_auth=True,
            database_url="sqlite+aiosqlite:///:memory:",
            storage_root=root / "objects",
            rate_limit_per_minute=1000,
            monthly_usage_quota=1000,
        )
        application = create_app(settings)
        await init_db(application.state.engine)
        yield application
        await application.state.engine.dispose()


@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as test_client:
        yield test_client
