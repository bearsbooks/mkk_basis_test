"""Общие фикстуры тестового стенда.

PostgreSQL поднимается в Docker через testcontainers на всю сессию тестов,
миграции Alembic применяются к чистой базе подпроцессом (проверяется реальный
CLI-путь запуска миграций). Каждый тест получает очищенные таблицы.
"""

import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

# Корень проекта — нужен для запуска Alembic в подпроцессе.
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def postgres_container():
    """Запускает эфемерный PostgreSQL 16 в Docker на всю тестовую сессию."""
    with PostgresContainer("postgres:16-alpine", driver="asyncpg") as container:
        yield container


@pytest.fixture(scope="session")
def database_url(postgres_container) -> str:
    """Применяет миграции Alembic к чистой тестовой базе и возвращает URL подключения."""
    url = postgres_container.get_connection_url()
    _run_alembic_upgrade(url)
    return url


def _run_alembic_upgrade(db_url: str) -> None:
    """Запускает `alembic upgrade head` подпроцессом, подставляя URL тестовой базы.

    Подпроцесс используется намеренно: он проверяет реальный путь запуска миграций
    и не конфликтует с событийным циклом pytest-asyncio.
    """
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-x", f"db_url={db_url}", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Alembic upgrade head завершился с ошибкой:\n{result.stdout}\n{result.stderr}"
    )


@pytest_asyncio.fixture
async def db_engine(database_url):
    """Создаёт асинхронный движок и очищает таблицы после каждого теста."""
    engine = create_async_engine(database_url)
    yield engine
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE TABLE payments, outbox"))
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    """Возвращает асинхронную сессию БД, привязанную к движку текущего теста."""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
