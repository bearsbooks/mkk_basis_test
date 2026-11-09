"""Юнит-тесты аутентификации по статическому API-ключу (заголовок X-API-Key).

Зависимость verify_api_key проверяется на изолированном FastAPI-приложении
с одним защищённым эндпоинтом.
"""

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import verify_api_key
from app.config import Settings, get_settings


def _build_test_app() -> FastAPI:
    """Собирает мини-приложение с одним эндпоинтом, защищённым зависимостью verify_api_key."""
    test_app = FastAPI()

    @test_app.get("/protected")
    async def protected(_: None = Depends(verify_api_key)) -> dict:
        """Заглушка защищённого эндпоинта: возвращает успех, если аутентификация пройдена."""
        return {"ok": True}

    # Подменяем настройки тестовым API-ключом.
    test_app.dependency_overrides[get_settings] = lambda: Settings(api_key="test-secret-key")
    return test_app


@pytest.fixture
async def client() -> AsyncClient:
    """Асинхронный HTTP-клиент поверх тестового приложения (без реальной сети)."""
    transport = ASGITransport(app=_build_test_app())
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


class TestApiKeyAuth:
    """Проверки зависимости аутентификации X-API-Key."""

    async def test_missing_header_returns_401(self, client: AsyncClient):
        """Запрос без заголовка X-API-Key отклоняется кодом 401."""
        response = await client.get("/protected")
        assert response.status_code == 401

    async def test_wrong_key_returns_401(self, client: AsyncClient):
        """Запрос с неверным значением ключа отклоняется кодом 401."""
        response = await client.get("/protected", headers={"X-API-Key": "wrong-key"})
        assert response.status_code == 401

    async def test_correct_key_passes(self, client: AsyncClient):
        """Запрос с корректным ключом проходит аутентификацию (200 и полезная нагрузка)."""
        response = await client.get("/protected", headers={"X-API-Key": "test-secret-key"})
        assert response.status_code == 200
        assert response.json() == {"ok": True}
