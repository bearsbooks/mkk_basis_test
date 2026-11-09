"""Фикстуры API-тестов: приложение с зависимостями, привязанными к тестовой БД."""

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings, get_settings
from app.db.session import get_db
from app.main import create_app

# API-ключ, подставляемый в тестовые настройки приложения.
TEST_API_KEY = "test-api-key"


@pytest.fixture
def app(db_engine) -> FastAPI:
    """Собирает приложение, переопределяя БД и настройки на тестовые."""
    application = create_app()
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_get_db():
        """Отдаёт сессию тестовой БД вместо продовой (yield-зависимость)."""
        async with session_factory() as session:
            yield session

    application.dependency_overrides[get_db] = override_get_db
    application.dependency_overrides[get_settings] = lambda: Settings(api_key=TEST_API_KEY)
    return application


@pytest_asyncio.fixture
async def client(app) -> AsyncClient:
    """HTTP-клиент поверх приложения через ASGI-транспорт (без реальной сети)."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        yield http_client


@pytest.fixture
def api_headers() -> dict[str, str]:
    """Корректные заголовки аутентификации и идемпотентности для запросов."""
    return {"X-API-Key": TEST_API_KEY, "Idempotency-Key": "test-idem-key"}
