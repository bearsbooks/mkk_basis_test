"""Точка входа API-сервиса: фабрика FastAPI-приложения с lifespan.

При старте приложения запускаются RabbitBroker (объявляет топологию)
и фоновая задача outbox-relay, гарантирующая публикацию событий.
Отдельная функция create_app упрощает тестирование — тесты собирают
приложение и подменяют зависимости (БД, настройки) без изменения модуля.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI

from app.api.v1.payments import router as payments_router
from app.config import Settings, get_settings
from app.consumers.payments import create_broker, payments_exchange, payments_queue
from app.db.session import SessionFactory
from app.services.outbox_relay import OutboxRelay

logger = logging.getLogger(__name__)


def _make_broker_publisher(broker: Any) -> Any:
    """Возвращает функцию публикации событий outbox в обменник payments.

    Замыкается на брокер приложения и адаптирует сигнатуру relay
    (payload, event_type) к вызову broker.publish.
    """

    async def publish(payload: dict, event_type: str) -> None:
        """Публикует событие в очередь payments.new обменника payments."""
        await broker.publish(payload, queue=payments_queue, exchange=payments_exchange)

    return publish


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Управляет фоновыми ресурсами API: брокер + outbox-relay.

    При старте — подключение к RabbitMQ и запуск цикла гарантированной
    публикации outbox-событий; при остановке — корректное завершение
    фоновой задачи и закрытие соединения с брокером.
    """
    settings: Settings = get_settings()
    broker = create_broker(settings.rabbitmq_url)
    await broker.start()

    relay = OutboxRelay(
        session_factory=SessionFactory,
        publisher=_make_broker_publisher(broker),
        batch_size=settings.outbox_batch_size,
    )
    relay_task = asyncio.create_task(
        relay.run_forever(interval=settings.outbox_poll_interval)
    )
    logger.info("API запущен: брокер подключён, outbox-relay работает")

    yield

    relay_task.cancel()
    try:
        await relay_task
    except asyncio.CancelledError:
        pass
    await broker.close()
    logger.info("API остановлен: relay остановлен, брокер закрыт")


def create_app() -> FastAPI:
    """Собирает FastAPI-приложение и подключает роутеры API версии 1."""
    application = FastAPI(
        title="MKK Payments",
        description="Асинхронный сервис процессинга платежей",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.include_router(payments_router)
    return application


# Модуль-уровень приложения для запуска: uvicorn app.main:app
app = create_app()
