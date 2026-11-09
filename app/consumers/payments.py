"""Consumer микросервиса: подписчик на очередь payments.new (FastStream).

Топология RabbitMQ:
- direct-обменник `payments` с очередью `payments.new`
  (аргументы x-dead-letter-exchange/x-dead-letter-routing-key);
- direct-обменник `payments.dlx` с очередью `payments.dlq` —
  сюда попадают сообщения, не обработанные после 3 повторных попыток.

Обработчик обёрнут в retry_with_backoff (3 попытки, экспоненциальная
задержка); после исчерпания попыток исключение пробрасывается, FastStream
отклоняет сообщение (nack без requeue), и RabbitMQ переносит его в DLQ.

Зависимости (session_factory, gateway, webhook_sender) вынесены на уровень
модуля — это позволяет тестам подменять их через monkeypatch.
"""

import logging
import uuid

import httpx
from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, RabbitQueue
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db.session import SessionFactory as prod_session_factory
from app.gateway.emulator import PaymentGatewayEmulator
from app.schemas.messages import PaymentsNewMessage
from app.services.payment_processor import PaymentProcessor
from app.services.payment_service import PAYMENTS_NEW_EVENT
from app.utils.retry import retry_with_backoff
from app.webhooks.sender import WebhookSender

logger = logging.getLogger(__name__)

# Зависимости обработчика; в тестах подменяются через monkeypatch.
session_factory: async_sessionmaker = prod_session_factory
gateway: PaymentGatewayEmulator = PaymentGatewayEmulator()
webhook_sender: WebhookSender = WebhookSender(
    client=httpx.AsyncClient(timeout=10.0),
)


def _build_topology(
    settings,
) -> tuple[RabbitExchange, RabbitQueue, RabbitExchange, RabbitQueue]:
    """Строит сущности топологии RabbitMQ: обменник, рабочую очередь, DLX и DLQ."""
    payments_exchange = RabbitExchange(
        settings.payments_exchange, type=ExchangeType.DIRECT, durable=True
    )
    # Рабочая очередь с настройкой мёртвых писем: nack → обменник payments.dlx.
    payments_queue = RabbitQueue(
        settings.payments_queue,
        routing_key=PAYMENTS_NEW_EVENT,
        durable=True,
        arguments={
            "x-dead-letter-exchange": settings.payments_dlx_exchange,
            "x-dead-letter-routing-key": settings.payments_dlq_queue,
        },
    )
    dlx_exchange = RabbitExchange(
        settings.payments_dlx_exchange, type=ExchangeType.DIRECT, durable=True
    )
    dlq_queue = RabbitQueue(
        settings.payments_dlq_queue,
        routing_key=settings.payments_dlq_queue,
        durable=True,
    )
    return payments_exchange, payments_queue, dlx_exchange, dlq_queue


# Топология уровня модуля: используется прод-брокером, релеем и тестами.
payments_exchange, payments_queue, dlx_exchange, dlq_queue = _build_topology(
    get_settings()
)


def create_broker(rabbitmq_url: str) -> RabbitBroker:
    """Создаёт брокер с топологией и обработчиками (используется в проде и e2e)."""
    broker = RabbitBroker(rabbitmq_url)

    @broker.subscriber(payments_queue, payments_exchange)
    async def handle_payment_new(message: PaymentsNewMessage) -> None:
        """Единый обработчик очереди payments.new: делает всё.

        Получает сообщение → эмуляция шлюза → обновление статуса в БД →
        webhook с повторными попытками. Системные сбои повторяются
        до 3 раз с экспоненциальной задержкой, затем пробрасываются
        для попадания сообщения в DLQ.
        """
        logger.info("Получено сообщение о платеже %s", message.payment_id)
        await _process_with_retry(message.payment_id)

    @broker.subscriber(dlq_queue, dlx_exchange)
    async def handle_dead_letter(message: dict) -> None:
        """Наблюдатель DLQ: делегирует хуку on_dead_letter (логирование/e2e-тесты)."""
        on_dead_letter(message)

    return broker


def on_dead_letter(message: dict) -> None:
    """Хук наблюдателя DLQ: логирует окончательно упавшие сообщения.

    Вынесен на уровень модуля, чтобы e2e-тесты могли подменить его
    и перехватывать сообщения, реально попавшие в DLQ.
    """
    logger.error("Сообщение попало в DLQ после исчерпания попыток: %s", message)


async def _process_with_retry(payment_id: uuid.UUID) -> None:
    """Выполняет обработку платежа с повторными попытками (3, экспон. задержка).

    После исчерпания попыток последнее исключение пробрасывается —
    FastStream отклоняет сообщение, и RabbitMQ уносит его в DLQ.
    """
    settings = get_settings()

    async def _process_once() -> None:
        """Одна попытка обработки: сессия БД → процессор → process."""
        async with session_factory() as session:
            processor = PaymentProcessor(
                session=session, gateway=gateway, webhook_sender=webhook_sender
            )
            await processor.process(payment_id)

    await retry_with_backoff(
        _process_once,
        attempts=settings.consumer_max_attempts,
        base_delay=settings.consumer_backoff_base,
    )


# Прод-брокер с зарегистрированными обработчиками: faststream run app.consumers.payments:broker
broker: RabbitBroker = create_broker(get_settings().rabbitmq_url)
