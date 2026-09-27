import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from .models import Base


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create an async engine for SQLite development or PostgreSQL production."""
    connect_args: dict[str, Any] = {}
    kwargs: dict[str, Any] = {"echo": echo, "pool_pre_ping": True}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        if ":memory:" in database_url:
            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_size"] = 10
        kwargs["max_overflow"] = 20
    engine = create_async_engine(database_url, connect_args=connect_args, **kwargs)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


async def init_db(engine: AsyncEngine) -> None:
    """Create development tables. Production uses Alembic migrations."""
    database_url = str(engine.url)
    if database_url.startswith("sqlite") and ":memory:" not in database_url:
        await asyncio.to_thread(Path("./data").mkdir, parents=True, exist_ok=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def get_session_from_factory(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
