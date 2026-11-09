"""Интеграционные тесты consumer-а через TestRabbitBroker (in-memory брокер).

Сообщения публикуются в тестовый брокер, обработчик выполняется с
подменёнными зависимостями (БД testcontainers, детерминированный шлюз,
шпион webhook-отправителя) — проверяется полный путь сообщения.
"""

import uuid
from decimal import Decimal

import pytest
from faststream.rabbit import TestRabbitBroker
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.consumers.payments as payments_module
from app.config import Settings
from app.schemas.payment import Currency, PaymentCreate, PaymentStatus
from app.services.payment_service import PaymentService

from tests.consumers.test_payment_processor import SpyWebhookSender, StubGateway


async def _create_pending_payment(session_factory) -> uuid.UUID:
    """Создаёт платёж в БД напрямую через сервис и возвращает его идентификатор."""
    async with session_factory() as session:
        service = PaymentService(session)
        payment = await service.create_payment(
            PaymentCreate(
                amount=Decimal("99.99"),
                currency=Currency.USD,
                description="Подписка",
                metadata={},
                webhook_url="https://client.example.com/hook",
            ),
            idempotency_key=f"cons-{uuid.uuid4()}",
        )
        return payment.id


@pytest.fixture
def consumer_env(db_engine, monkeypatch):
    """Подменяет зависимости модуля consumer на тестовые (сессии, шлюз, webhook)."""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    gateway = StubGateway()
    sender = SpyWebhookSender()
    monkeypatch.setattr(payments_module, "session_factory", session_factory)
    monkeypatch.setattr(payments_module, "gateway", gateway)
    monkeypatch.setattr(payments_module, "webhook_sender", sender)
    # Мгновенные задержки retry, чтобы тесты не ждали секунды.
    monkeypatch.setattr(
        payments_module,
        "get_settings",
        lambda: Settings(consumer_backoff_base=0.001, consumer_max_attempts=3),
    )
    return {"session_factory": session_factory, "gateway": gateway, "sender": sender}


class TestPaymentsNewConsumer:
    """Обработчик очереди payments.new через in-memory брокер."""

    async def test_message_processing_updates_db_and_sends_webhook(self, consumer_env):
        """Сообщение из брокера: платёж succeeded, webhook доставлен."""
        payment_id = await _create_pending_payment(consumer_env["session_factory"])

        async with TestRabbitBroker(payments_module.broker) as test_broker:
            await test_broker.publish(
                {"payment_id": str(payment_id)},
                queue=payments_module.payments_queue,
                exchange=payments_module.payments_exchange,
            )

        async with consumer_env["session_factory"]() as session:
            payment = await session.get(payments_module.Payment, payment_id)
            assert payment.status is PaymentStatus.SUCCEEDED
            assert payment.processed_at is not None
        assert len(consumer_env["sender"].calls) == 1
        assert consumer_env["sender"].calls[0][1].event == "payment.succeeded"

    async def test_gateway_failure_message_marks_payment_failed(self, consumer_env, monkeypatch):
        """Отказ шлюза из сообщения: платёж failed, webhook payment.failed."""
        consumer_env["gateway"].fail = True
        payment_id = await _create_pending_payment(consumer_env["session_factory"])

        async with TestRabbitBroker(payments_module.broker) as test_broker:
            await test_broker.publish(
                {"payment_id": str(payment_id)},
                queue=payments_module.payments_queue,
                exchange=payments_module.payments_exchange,
            )

        async with consumer_env["session_factory"]() as session:
            payment = await session.get(payments_module.Payment, payment_id)
            assert payment.status is PaymentStatus.FAILED
        assert consumer_env["sender"].calls[0][1].event == "payment.failed"

    async def test_redelivery_of_processed_payment_is_noop(self, consumer_env):
        """Повторная доставка обработанного сообщения ничего не меняет (идемпотентность)."""
        payment_id = await _create_pending_payment(consumer_env["session_factory"])

        async with TestRabbitBroker(payments_module.broker) as test_broker:
            await test_broker.publish(
                {"payment_id": str(payment_id)},
                queue=payments_module.payments_queue,
                exchange=payments_module.payments_exchange,
            )
            # Повторная доставка того же сообщения.
            await test_broker.publish(
                {"payment_id": str(payment_id)},
                queue=payments_module.payments_queue,
                exchange=payments_module.payments_exchange,
            )

        # Webhook отправлен ровно один раз — дубль не вызвал повторной обработки.
        assert len(consumer_env["sender"].calls) == 1

    async def test_system_error_retried_three_times_then_raised_for_dlq(
        self, consumer_env, monkeypatch
    ):
        """Системная ошибка: 3 попытки с retry, затем проброс для DLQ."""

        attempts = []

        async def failing_factory():
            """«Фабрика сессий», всегда падающая при открытии сессии."""
            attempts.append(1)
            raise RuntimeError("БД недоступна")

        monkeypatch.setattr(payments_module, "session_factory", failing_factory)

        async with TestRabbitBroker(payments_module.broker) as test_broker:
            with pytest.raises(RuntimeError, match="БД недоступна"):
                await test_broker.publish(
                    {"payment_id": str(uuid.uuid4())},
                    queue=payments_module.payments_queue,
                    exchange=payments_module.payments_exchange,
                )

        # Ровно три попытки — дальше сообщение уходит в DLQ.
        assert len(attempts) == 3
