"""Тесты жизненного цикла приложения (lifespan): брокер + outbox-relay.

Брокер подменяется дублем; проверяется, что при старте приложения
relay действительно публикует ожидающие outbox-события, а при остановке
брокер корректно закрывается.
"""

import asyncio
import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.main as main_module
from app.config import Settings
from app.main import create_app
from app.schemas.payment import Currency, PaymentCreate
from app.services.payment_service import PaymentService


class FakeBroker:
    """Дубль RabbitBroker: фиксирует start/stop и публикуемые сообщения."""

    def __init__(self) -> None:
        """Инициализирует состояние дубля брокера."""
        self.started: bool = False
        self.stopped: bool = False
        self.published: list[dict] = []

    async def start(self) -> None:
        """Имитирует успешное подключение к брокеру."""
        self.started = True

    async def stop(self) -> None:
        """Имитирует корректное закрытие соединения."""
        self.stopped = True

    async def publish(self, payload: dict, queue=None, exchange=None) -> None:
        """Записывает опубликованное сообщение вместо реальной отправки."""
        self.published.append(payload)


@pytest_asyncio.fixture
async def lifespan_app(db_engine, monkeypatch):
    """Собирает приложение с дублем брокера и быстрой настройкой relay."""
    fake_broker = FakeBroker()
    monkeypatch.setattr(main_module, "create_broker", lambda url: fake_broker)
    monkeypatch.setattr(
        main_module,
        "get_settings",
        lambda: Settings(outbox_poll_interval=0.01, outbox_batch_size=10),
    )
    monkeypatch.setattr(
        main_module,
        "SessionFactory",
        async_sessionmaker(db_engine, expire_on_commit=False),
    )
    return create_app(), fake_broker


class TestLifespan:
    """Проверки запуска и остановки фоновых ресурсов приложения."""

    async def test_lifespan_starts_broker_and_publishes_outbox(self, lifespan_app, db_engine):
        """Внутри lifespan брокер стартует, relay публикует ожидающие события."""
        application, fake_broker = lifespan_app

        # Готовим платёж с outbox-событием ДО запуска приложения.
        session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with session_factory() as session:
            payment = await PaymentService(session).create_payment(
                PaymentCreate(
                    amount=Decimal("77.70"),
                    currency=Currency.RUB,
                    description="Платёж для relay",
                    metadata={},
                    webhook_url="https://client.example.com/hook",
                ),
                idempotency_key=f"life-{uuid.uuid4()}",
            )

        async with application.router.lifespan_context(application):
            assert fake_broker.started
            # Ждём, пока relay-поллер опубликует событие (интервал 10 мс).
            for _ in range(100):
                if fake_broker.published:
                    break
                await asyncio.sleep(0.02)

        assert fake_broker.stopped
        assert len(fake_broker.published) == 1
        assert fake_broker.published[0]["payment_id"] == str(payment.id)

    async def test_lifespan_stops_relay_task_cleanly(self, lifespan_app):
        """После выхода из lifespan фоновая задача relay остановлена (не пишет)."""
        application, fake_broker = lifespan_app

        async with application.router.lifespan_context(application):
            await asyncio.sleep(0.05)

        published_after_stop = len(fake_broker.published)
        await asyncio.sleep(0.05)
        assert len(fake_broker.published) == published_after_stop
