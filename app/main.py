"""Точка входа API-сервиса: фабрика FastAPI-приложения.

Отдельная функция create_app упрощает тестирование — тесты собирают
приложение и подменяют зависимости (БД, настройки) без изменения модуля.
"""

from fastapi import FastAPI

from app.api.v1.payments import router as payments_router


def create_app() -> FastAPI:
    """Собирает FastAPI-приложение и подключает роутеры API версии 1."""
    application = FastAPI(
        title="MKK Payments",
        description="Асинхронный сервис процессинга платежей",
        version="0.1.0",
    )
    application.include_router(payments_router)
    return application


# Модуль-уровень приложения для запуска: uvicorn app.main:app
app = create_app()
