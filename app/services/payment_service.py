"""Сервис платежей: бизнес-логика создания и чтения с реализацией outbox pattern.

Ключевой инвариант: платёж и событие payments.new записываются
в ОДНУ транзакцию — тогда событие гарантированно существует,
и фоновый relay опубликует его в RabbitMQ.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxMessage, Payment
from app.schemas.payment import PaymentCreate, PaymentStatus

# Тип события о новом платеже, публикуемого в брокер.
PAYMENTS_NEW_EVENT: str = "payments.new"


class PaymentService:
    """Инкапсулирует операции над платежами поверх асинхронной сессии БД."""

    def __init__(self, session: AsyncSession) -> None:
        """Сохраняет сессию БД, в рамках которой выполняются все операции сервиса."""
        self._session = session

    async def create_payment(self, data: PaymentCreate, idempotency_key: str) -> Payment:
        """Создаёт платёж (pending) и outbox-событие в одной транзакции.

        Идемпотентность: если платёж с таким ключом уже существует,
        возвращается ранее созданный платёж без побочных эффектов.
        На случай гонки (два параллельных запроса) полагаемся
        на уникальный индекс idempotency_key: при IntegrityError
        откатываемся и перечитываем существующую запись.
        """
        existing = await self._find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing

        # Идентификатор генерируем сразу: он нужен и платежу, и outbox-событию.
        payment_id = uuid.uuid4()
        payment = Payment(
            id=payment_id,
            amount=data.amount,
            currency=data.currency,
            description=data.description,
            metadata_=data.metadata,
            webhook_url=data.webhook_url,
            status=PaymentStatus.PENDING,
            idempotency_key=idempotency_key,
        )
        outbox = OutboxMessage(
            event_type=PAYMENTS_NEW_EVENT,
            payload={
                "payment_id": str(payment_id),
                "amount": str(data.amount),
                "currency": data.currency.value,
            },
            aggregate_id=payment_id,
        )

        self._session.add(payment)
        self._session.add(outbox)
        try:
            await self._session.commit()
        except IntegrityError:
            # Гонка: конкурентный запрос успел вставить платёж с тем же ключом.
            await self._session.rollback()
            existing = await self._find_by_idempotency_key(idempotency_key)
            if existing is None:
                raise
            return existing

        await self._session.refresh(payment)
        return payment

    async def get_payment(self, payment_id: uuid.UUID) -> Payment | None:
        """Возвращает платёж по идентификатору или None, если он не найден."""
        return await self._session.get(Payment, payment_id)

    async def _find_by_idempotency_key(self, idempotency_key: str) -> Payment | None:
        """Ищет платёж по уникальному ключу идемпотентности."""
        return await self._session.scalar(
            select(Payment).where(Payment.idempotency_key == idempotency_key)
        )
