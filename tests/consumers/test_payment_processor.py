"""Тесты процессора платежей: единый обработчик из задания.

Процессор делает всё: эмуляция шлюза → обновление статуса в БД →
webhook-уведомление. Тесты идут против реальной БД (testcontainers),
шлюз и webhook-отправитель подменяются детерминированными дублёрами.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from app.gateway.emulator import PaymentGatewayError
from app.models import Payment
from app.schemas.payment import Currency, PaymentCreate, PaymentStatus
from app.services.payment_processor import PaymentProcessor
from app.services.payment_service import PaymentService


class StubGateway:
    """Дубль шлюза с управляемым исходом обработки."""

    def __init__(self, fail: bool = False) -> None:
        """Задаёт исход: fail=True — всегда отказ шлюза."""
        self.fail = fail
        self.calls: list[uuid.UUID] = []

    async def process_payment(self, payment_id: uuid.UUID) -> None:
        """Имитирует обработку: запоминает вызов и при необходимости падает."""
        self.calls.append(payment_id)
        if self.fail:
            raise PaymentGatewayError(f"Шлюз отклонил платёж {payment_id}")


class SpyWebhookSender:
    """Дубль WebhookSender: записывает все отправки, исход настраивается."""

    def __init__(self, delivered: bool = True) -> None:
        """Задаёт результат доставки (по умолчанию — успешно)."""
        self.delivered = delivered
        self.calls: list[tuple[str, object]] = []

    async def send(self, url: str, payload) -> bool:
        """Фиксирует вызов и возвращает заданный результат доставки."""
        self.calls.append((url, payload))
        return self.delivered


async def _create_pending_payment(db_session) -> Payment:
    """Создаёт платёж в статусе pending напрямую через сервис."""
    service = PaymentService(db_session)
    return await service.create_payment(
        PaymentCreate(
            amount=Decimal("150.00"),
            currency=Currency.RUB,
            description="Оплата заказа №42",
            metadata={"order_id": 42},
            webhook_url="https://client.example.com/webhooks",
        ),
        idempotency_key=f"proc-{uuid.uuid4()}",
    )


class TestPaymentProcessorSuccess:
    """Сценарий успешной обработки платежа."""

    async def test_gateway_success_marks_succeeded(self, db_session):
        """Успех шлюза: статус succeeded, processed_at заполнен."""
        payment = await _create_pending_payment(db_session)
        gateway = StubGateway(fail=False)
        processor = PaymentProcessor(db_session, gateway=gateway, webhook_sender=SpyWebhookSender())

        await processor.process(payment.id)

        await db_session.refresh(payment)
        assert payment.status is PaymentStatus.SUCCEEDED
        assert payment.processed_at is not None

    async def test_webhook_sent_with_succeeded_event(self, db_session):
        """После успеха на webhook_url уходит уведомление payment.succeeded."""
        payment = await _create_pending_payment(db_session)
        sender = SpyWebhookSender()
        processor = PaymentProcessor(db_session, gateway=StubGateway(), webhook_sender=sender)

        await processor.process(payment.id)

        assert len(sender.calls) == 1
        url, payload = sender.calls[0]
        assert url == "https://client.example.com/webhooks"
        assert payload.event == "payment.succeeded"
        assert str(payload.payment_id) == str(payment.id)
        assert payload.amount == Decimal("150.00")
        assert payload.currency is Currency.RUB


class TestPaymentProcessorFailure:
    """Сценарий отказа шлюза (бизнес-ошибка, а не системный сбой)."""

    async def test_gateway_failure_marks_failed(self, db_session):
        """Отказ шлюза: статус failed, но исключение наружу не пробрасывается."""
        payment = await _create_pending_payment(db_session)
        processor = PaymentProcessor(
            db_session, gateway=StubGateway(fail=True), webhook_sender=SpyWebhookSender()
        )

        await processor.process(payment.id)

        await db_session.refresh(payment)
        assert payment.status is PaymentStatus.FAILED
        assert payment.processed_at is not None

    async def test_webhook_sent_with_failed_event(self, db_session):
        """После отказа на webhook уходит уведомление payment.failed."""
        payment = await _create_pending_payment(db_session)
        sender = SpyWebhookSender()
        processor = PaymentProcessor(db_session, gateway=StubGateway(fail=True), webhook_sender=sender)

        await processor.process(payment.id)

        assert len(sender.calls) == 1
        assert sender.calls[0][1].event == "payment.failed"
        assert sender.calls[0][1].status == PaymentStatus.FAILED

    async def test_webhook_delivery_failure_keeps_payment_processed(self, db_session):
        """Недоставка webhook не меняет итог обработки: платёж остаётся succeeded."""
        payment = await _create_pending_payment(db_session)
        processor = PaymentProcessor(
            db_session,
            gateway=StubGateway(),
            webhook_sender=SpyWebhookSender(delivered=False),
        )

        # Не должно бросить исключение: недоставка уведомления — не сбой обработки.
        await processor.process(payment.id)

        await db_session.refresh(payment)
        assert payment.status is PaymentStatus.SUCCEEDED


class TestPaymentProcessorIdempotency:
    """Идемпотентность обработчика при повторной доставке сообщения."""

    async def test_terminal_payment_is_skipped(self, db_session):
        """Платёж в терминальном статусе не обрабатывается повторно."""
        payment = await _create_pending_payment(db_session)
        payment.status = PaymentStatus.SUCCEEDED
        payment.processed_at = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
        await db_session.commit()

        gateway = StubGateway()
        sender = SpyWebhookSender()
        processor = PaymentProcessor(db_session, gateway=gateway, webhook_sender=sender)

        await processor.process(payment.id)

        assert gateway.calls == []
        assert sender.calls == []
        await db_session.refresh(payment)
        assert payment.status is PaymentStatus.SUCCEEDED

    async def test_unknown_payment_id_is_ignored(self, db_session):
        """Сообщение с несуществующим payment_id игнорируется без исключения."""
        processor = PaymentProcessor(
            db_session, gateway=StubGateway(), webhook_sender=SpyWebhookSender()
        )

        # Не должно бросить: «ядовитое» сообщение просто пропускается.
        await processor.process(uuid.uuid4())
