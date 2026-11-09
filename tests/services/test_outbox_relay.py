"""Тесты outbox-relay: фоновая гарантированная публикация событий в брокер.

Relay выбирает неопубликованные outbox-записи, публикует их
и проставляет published_at только после успешной публикации.
Тесты идут против реальной БД, брокер подменяется записывающим дублёром.
"""

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import OutboxMessage
from app.schemas.payment import Currency, PaymentCreate
from app.services.outbox_relay import OutboxRelay
from app.services.payment_service import PAYMENTS_NEW_EVENT, PaymentService


class RecordingPublisher:
    """Дубль брокера: записывает все публикации, опционально падает."""

    def __init__(self, fail: bool = False) -> None:
        """Настраивает исход публикации (по умолчанию — успех)."""
        self.fail = fail
        self.calls: list[tuple[dict, str]] = []

    async def __call__(self, payload: dict, event_type: str) -> None:
        """Фиксирует публикацию и при необходимости имитирует сбой брокера."""
        self.calls.append((payload, event_type))
        if self.fail:
            raise RuntimeError("брокер недоступен")


async def _seed_payments(session_factory, count: int) -> None:
    """Создаёт `count` платежей через сервис — каждый со своей outbox-записью."""
    async with session_factory() as session:
        service = PaymentService(session)
        for i in range(count):
            await service.create_payment(
                PaymentCreate(
                    amount=Decimal("10.00") + i,
                    currency=Currency.EUR,
                    description=f"Платёж #{i}",
                    metadata={},
                    webhook_url="https://client.example.com/hook",
                ),
                idempotency_key=f"relay-{uuid.uuid4()}",
            )


@pytest.fixture
def session_factory(db_engine) -> async_sessionmaker:
    """Фабрика сессий поверх тестового движка."""
    return async_sessionmaker(db_engine, expire_on_commit=False)


class TestOutboxRelay:
    """Проверки одного прохода публикации publish_pending."""

    async def test_publishes_pending_messages_and_marks_published(
        self, session_factory, db_session
    ):
        """Неопубликованные события уходят в брокер и получают published_at."""
        await _seed_payments(session_factory, count=2)
        publisher = RecordingPublisher()
        relay = OutboxRelay(session_factory=session_factory, publisher=publisher)

        published = await relay.publish_pending()

        assert published == 2
        assert len(publisher.calls) == 2
        payload, event_type = publisher.calls[0]
        assert event_type == PAYMENTS_NEW_EVENT
        assert "payment_id" in payload

        from sqlalchemy import select

        rows = (await db_session.execute(select(OutboxMessage))).scalars().all()
        assert all(row.published_at is not None for row in rows)

    async def test_second_pass_publishes_nothing(self, session_factory):
        """Повторный проход не публикует уже опубликованные события."""
        await _seed_payments(session_factory, count=1)
        publisher = RecordingPublisher()
        relay = OutboxRelay(session_factory=session_factory, publisher=publisher)

        await relay.publish_pending()
        second = await relay.publish_pending()

        assert second == 0
        assert len(publisher.calls) == 1

    async def test_publisher_failure_keeps_messages_pending(self, session_factory, db_session):
        """Сбой брокера: событие остаётся неопубликованным и уйдёт в следующем проходе."""
        await _seed_payments(session_factory, count=1)
        failing = RecordingPublisher(fail=True)
        relay = OutboxRelay(session_factory=session_factory, publisher=failing)

        with pytest.raises(RuntimeError, match="брокер недоступен"):
            await relay.publish_pending()

        from sqlalchemy import select

        rows = (await db_session.execute(select(OutboxMessage))).scalars().all()
        assert all(row.published_at is None for row in rows)

        # Брокер «восстановился» — событие успешно публикуется со второй попытки.
        healthy = RecordingPublisher()
        relay = OutboxRelay(session_factory=session_factory, publisher=healthy)
        assert await relay.publish_pending() == 1

    async def test_batch_size_limits_single_pass(self, session_factory):
        """За один проход публикуется не более batch_size событий."""
        await _seed_payments(session_factory, count=3)
        publisher = RecordingPublisher()
        relay = OutboxRelay(session_factory=session_factory, publisher=publisher, batch_size=2)

        first = await relay.publish_pending()
        second = await relay.publish_pending()

        assert first == 2
        assert second == 1

    async def test_messages_published_in_creation_order(self, session_factory):
        """События публикуются в порядке создания (FIFO по created_at)."""
        await _seed_payments(session_factory, count=3)
        publisher = RecordingPublisher()
        relay = OutboxRelay(session_factory=session_factory, publisher=publisher)

        await relay.publish_pending()

        amounts = [payload["amount"] for payload, _ in publisher.calls]
        assert amounts == ["10.00", "11.00", "12.00"]


class TestOutboxRelayLoop:
    """Проверки бесконечного цикла опроса run_forever."""

    async def test_run_forever_publishes_until_cancelled(self, session_factory):
        """Цикл публикует новые события, пока его не отменят (без исключений)."""
        await _seed_payments(session_factory, count=1)
        publisher = RecordingPublisher()
        relay = OutboxRelay(session_factory=session_factory, publisher=publisher)

        task = asyncio.create_task(relay.run_forever(interval=0.01))
        # Ждём, пока хотя бы одно событие будет опубликовано, затем отменяем.
        for _ in range(100):
            if publisher.calls:
                break
            await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert len(publisher.calls) == 1

    async def test_run_forever_survives_publisher_failure(self, session_factory):
        """Сбой брокера в одном проходе не останавливает цикл опроса."""
        flaky = RecordingPublisher(fail=True)
        relay = OutboxRelay(session_factory=session_factory, publisher=flaky)

        task = asyncio.create_task(relay.run_forever(interval=0.01))
        await asyncio.sleep(0.05)
        assert not task.done(), "цикл не должен падать из-за ошибки публикации"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
