"""Async + sync SQLAlchemy engines. The API uses asyncpg; Celery workers use psycopg (sync)."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


async_engine = create_async_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=10)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False, class_=AsyncSession)

_sync_engine = None
_SyncSessionLocal = None


def sync_engine():
    global _sync_engine, _SyncSessionLocal
    if _sync_engine is None:
        _sync_engine = create_engine(settings.sync_db_url, pool_pre_ping=True, pool_size=5, max_overflow=5)
        _SyncSessionLocal = sessionmaker(_sync_engine, expire_on_commit=False)
    return _sync_engine


def sync_session() -> Session:
    sync_engine()
    return _SyncSessionLocal()  # type: ignore[misc]


async def get_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
