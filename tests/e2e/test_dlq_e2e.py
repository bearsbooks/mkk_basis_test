"""Сквозной e2e-тест Dead Letter Queue на реальном RabbitMQ (testcontainers).

Проверяет полную цепочку отказов: сообщение падает 3 раза с retry →
FastStream отклоняет его (nack без requeue) → RabbitMQ переносит
сообщение в очередь payments.dlq через обменник payments.dlx.

Запуск: pytest -m e2e (требуется запущенный Docker).
"""

import asyncio
import uuid

import pytest
from testcontainers.community.rabbitmq import RabbitMqContainer

import app.consumers.payments as payments_module
from app.config import Settings
from app.consumers.payments import create_broker

# Все тесты модуля — e2e (отключены в обычном прогоне через addopts).
pytestmark = pytest.mark.e2e


class FailingSessionFactory:
    """Фабрика сессий, всегда падающая — форсирует системный сбой обработки."""

    def __init__(self) -> None:
        """Инициализирует счётчик попыток."""
        self.attempts: list[int] = []

    def __call__(self):
        """Фиксирует попытку и возвращает «падающий» контекстный менеджер."""
        self.attempts.append(1)

        class _FailingContext:
            """Контекст сессии, бросающий исключение при открытии."""

            async def __aenter__(self):
                """Имитирует ошибку подключения к БД."""
                raise RuntimeError("БД недоступна (e2e)")

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                """Не подавляет исключения."""
                return False

        return _FailingContext()


@pytest.fixture(scope="module")
def rabbitmq_url():
    """Поднимает эфемерный RabbitMQ 3.13 в Docker на время модуля тестов.

    Возвращает amqp-URL, собранный из параметров подключения контейнера.
    """
    with RabbitMqContainer("rabbitmq:3.13-management-alpine") as rabbitmq:
        params = rabbitmq.get_connection_params()
        credentials = params.credentials
        yield (
            f"amqp://{credentials.username}:{credentials.password}"
            f"@{params.host}:{params.port}/{params.virtual_host}"
        )


class TestDeadLetterQueueE2E:
    """Проверка реального попадания сообщения в DLQ после 3 неудач."""

    async def test_failed_message_lands_in_dlq_after_three_attempts(
        self, rabbitmq_url, monkeypatch
    ):
        """Сообщение с системным сбоем: 3 попытки → перенос в payments.dlq."""
        failing_factory = FailingSessionFactory()
        dead_letters: list[dict] = []
        dead_letter_event = asyncio.Event()

        def spy_on_dead_letter(message: dict) -> None:
            """Хук-шпион: фиксирует сообщение из DLQ и сигнализирует событием."""
            dead_letters.append(message)
            dead_letter_event.set()

        monkeypatch.setattr(payments_module, "session_factory", failing_factory)
        monkeypatch.setattr(payments_module, "on_dead_letter", spy_on_dead_letter)
        # Мгновенные задержки retry, чтобы e2e-тест не ждал 2+4 секунды.
        monkeypatch.setattr(
            payments_module,
            "get_settings",
            lambda: Settings(consumer_backoff_base=0.05, consumer_max_attempts=3),
        )

        broker = create_broker(rabbitmq_url)
        try:
            await broker.start()

            payment_id = str(uuid.uuid4())
            await broker.publish(
                {"payment_id": payment_id},
                queue=payments_module.payments_queue,
                exchange=payments_module.payments_exchange,
            )

            # Ждём попадания в DLQ (3 попытки × задержки + перенос RabbitMQ).
            await asyncio.wait_for(dead_letter_event.wait(), timeout=30.0)
        finally:
            await broker.stop()

        # Ровно три попытки обработки до попадания в DLQ.
        assert len(failing_factory.attempts) == 3
        assert len(dead_letters) == 1
        assert dead_letters[0]["payment_id"] == payment_id
