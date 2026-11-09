"""Асинхронные движок и фабрика сессий SQLAlchemy, зависимость get_db.

Движок создаётся один раз на процесс (pool переиспользуется между запросами),
а зависимость get_db выдаёт каждому HTTP-запросу свою сессию.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

# Единый на процесс асинхронный движок (подключения ленивые — создаётся мгновенно).
engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)

# Фабрика сессий: expire_on_commit=False, чтобы объекты оставались читаемыми после коммита.
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI-зависимость: сессия БД на время запроса с гарантией закрытия."""
    async with SessionFactory() as session:
        yield session
