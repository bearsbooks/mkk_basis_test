"""Тесты сервиса платежей: создание с outbox-событием, идемпотентность, чтение.

Сервис — сердце записи: платёж и событие outbox обязаны попадать
в одну транзакцию, а повторный вызов с тем же ключом не должен
создавать дубликаты.
"""

import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.models import OutboxMessage, Payment
from app.schemas.payment import Currency, PaymentCreate, PaymentStatus
from app.services.payment_service import PAYMENTS_NEW_EVENT, PaymentService


def _build_create_request(amount: str = "100.50") -> PaymentCreate:
    """Готовит валидный запрос на создание платежа с заданной суммой."""
    return PaymentCreate(
        amount=Decimal(amount),
        currency=Currency.RUB,
        description="Оплата заказа №42",
        metadata={"order_id": 42},
        webhook_url="https://client.example.com/webhooks",
    )


class TestCreatePayment:
    """Проверки создания платежа через PaymentService."""

    async def test_creates_payment_with_pending_status(self, db_session):
        """Созданный платёж имеет статус pending и корректные поля."""
        service = PaymentService(db_session)
        payment = await service.create_payment(_build_create_request(), idempotency_key="key-1")

        assert payment.id is not None
        assert payment.status is PaymentStatus.PENDING
        assert payment.amount == Decimal("100.50")
        assert payment.currency is Currency.RUB
        assert payment.metadata_ == {"order_id": 42}
        assert payment.webhook_url == "https://client.example.com/webhooks"
        assert payment.created_at is not None
        assert payment.processed_at is None

    async def test_writes_outbox_event_in_same_transaction(self, db_session):
        """Вместе с платежём создаётся ровно одна запись outbox с событием payments.new."""
        service = PaymentService(db_session)
        payment = await service.create_payment(_build_create_request(), idempotency_key="key-2")

        outbox_rows = (await db_session.execute(select(OutboxMessage))).scalars().all()
        assert len(outbox_rows) == 1
        event = outbox_rows[0]
        assert event.event_type == PAYMENTS_NEW_EVENT
        assert event.aggregate_id == payment.id
        assert str(payment.id) == event.payload["payment_id"]
        assert event.published_at is None

    async def test_duplicate_idempotency_key_returns_existing_payment(self, db_session):
        """Повторное создание с тем же idempotency key возвращает тот же платёж.

        Дублей быть не должно: ни второго платежа, ни второго outbox-события.
        """
        service = PaymentService(db_session)
        first = await service.create_payment(_build_create_request(), idempotency_key="dup-key")
        second = await service.create_payment(
            _build_create_request(amount="999.99"), idempotency_key="dup-key"
        )

        assert second.id == first.id
        assert second.amount == Decimal("100.50")  # данные первого платежа не изменены
        payments_count = await db_session.scalar(select(func.count()).select_from(Payment))
        outbox_count = await db_session.scalar(select(func.count()).select_from(OutboxMessage))
        assert payments_count == 1
        assert outbox_count == 1


class TestGetPayment:
    """Проверки чтения платежа через PaymentService."""

    async def test_get_payment_returns_created_payment(self, db_session):
        """Платёж, созданный через сервис, читается по идентификатору."""
        service = PaymentService(db_session)
        created = await service.create_payment(_build_create_request(), idempotency_key="key-3")

        fetched = await service.get_payment(created.id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.idempotency_key == "key-3"

    async def test_get_payment_returns_none_for_unknown_id(self, db_session):
        """Для несуществующего идентификатора возвращается None."""
        service = PaymentService(db_session)
        assert await service.get_payment(uuid.uuid4()) is None
