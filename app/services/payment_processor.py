"""Процессор платежей: единый обработчик из задания.

Полный цикл обработки одного платежа: эмуляция внешнего шлюза →
обновление статуса в БД → webhook-уведомление клиента.

Разделение ошибок:
- отказ шлюза (PaymentGatewayError) — бизнес-исход: платёж переводится
  в failed, клиент уведомляется, исключение наружу не уходит;
- системные сбои (БД и т.п.) — пробрасываются наверх, чтобы обёртка
  retry в consumer-е повторила обработку, а после 3 неудач отправила
  сообщение в DLQ.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.emulator import PaymentGatewayError
from app.models import Payment
from app.schemas.payment import (
    PAYMENT_FAILED_EVENT,
    PAYMENT_SUCCEEDED_EVENT,
    PaymentStatus,
    WebhookPayload,
)

logger = logging.getLogger(__name__)


class GatewayLike(Protocol):
    """Минимальный интерфейс шлюза, нужный процессору."""

    async def process_payment(self, payment_id: uuid.UUID) -> None:
        """Обрабатывает платёж; отказ выражается исключением."""
        ...


class WebhookSenderLike(Protocol):
    """Минимальный интерфейс отправителя webhook-уведомлений."""

    async def send(self, url: str, payload: WebhookPayload) -> bool:
        """Доставляет уведомление; True — доставлено, False — нет."""
        ...


class PaymentProcessor:
    """Единый обработчик платежа: шлюз → статус → webhook."""

    def __init__(
        self,
        session: AsyncSession,
        gateway: GatewayLike,
        webhook_sender: WebhookSenderLike,
    ) -> None:
        """Сохраняет сессию БД и коллабораторов (шлюз, отправитель webhook)."""
        self._session = session
        self._gateway = gateway
        self._webhook_sender = webhook_sender

    async def process(self, payment_id: uuid.UUID) -> None:
        """Обрабатывает платёж от начала до конца; идемпотентен при дублях.

        Шаги: загрузка → эмуляция шлюза → статус + processed_at → коммит →
        webhook. Повторная доставка сообщения для уже обработанного платежа
        безопасно пропускается (статус терминален).
        """
        payment = await self._session.get(Payment, payment_id)
        if payment is None:
            # Сообщение ссылается на несуществующий платёж («ядовитое») — пропускаем.
            logger.warning("Платёж %s не найден, сообщение пропущено", payment_id)
            return
        if payment.status != PaymentStatus.PENDING:
            # Платёж уже обработан — повторная доставка ничего не меняет.
            logger.info(
                "Платёж %s уже в статусе %s, повторная доставка пропущена",
                payment_id,
                payment.status,
            )
            return

        try:
            await self._gateway.process_payment(payment_id)
            payment.status = PaymentStatus.SUCCEEDED
            event = PAYMENT_SUCCEEDED_EVENT
        except PaymentGatewayError as error:
            # Отказ шлюза — ожидаемый бизнес-исход, а не системный сбой.
            logger.warning("Шлюз отклонил платёж %s: %s", payment_id, error)
            payment.status = PaymentStatus.FAILED
            event = PAYMENT_FAILED_EVENT

        payment.processed_at = datetime.now(timezone.utc)
        await self._session.commit()

        await self._notify_client(payment, event)

    async def _notify_client(self, payment: Payment, event: str) -> None:
        """Отправляет webhook-уведомление о результате обработки.

        Недоставка уведомления не влияет на итог обработки платежа
        и не является системным сбоем (WebhookSender сам делает ретраи).
        """
        payload = WebhookPayload(
            event=event,
            payment_id=payment.id,
            status=payment.status,
            amount=payment.amount,
            currency=payment.currency,
            description=payment.description,
            processed_at=payment.processed_at,
        )
        delivered = await self._webhook_sender.send(payment.webhook_url, payload)
        if not delivered:
            logger.error(
                "Webhook для платежа %s не доставлен после всех попыток",
                payment.id,
            )
