"""Юнит-тесты отправителя webhook-уведомлений с повторными попытками.

HTTP-вызовы подменяются через respx, паузы — подделкой sleep,
поэтому тесты детерминированы и мгновенны.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest
import respx

from app.schemas.payment import PAYMENT_SUCCEEDED_EVENT, Currency, WebhookPayload
from app.webhooks.sender import WebhookSender

WEBHOOK_URL = "https://client.example.com/webhooks"


def _make_sleep_recorder() -> tuple[list[float], object]:
    """Создаёт подмену asyncio.sleep, записывающую длительности пауз."""

    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        """Сохраняет запрошенную длительность паузы вместо реального ожидания."""
        recorded.append(seconds)

    return recorded, fake_sleep


def _build_payload() -> WebhookPayload:
    """Готовит payload успешного платежа для отправки на webhook."""
    return WebhookPayload(
        event=PAYMENT_SUCCEEDED_EVENT,
        payment_id=uuid.uuid4(),
        status="succeeded",
        amount=Decimal("150.00"),
        currency=Currency.RUB,
        description="Оплата заказа №42",
        processed_at=datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc),
    )


def _make_sender(sleeper) -> WebhookSender:
    """Создаёт отправитель с максимальной изоляцией от внешнего мира."""
    return WebhookSender(
        client=httpx.AsyncClient(),
        max_attempts=3,
        backoff_base=1.0,
        sleeper=sleeper,
    )


class TestWebhookSender:
    """Проверки доставки уведомления на URL клиента."""

    @respx.mock
    async def test_success_on_first_attempt_returns_true(self):
        """2xx с первой попытки: уведомление доставлено, без пауз."""
        delays, sleeper = _make_sleep_recorder()
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        sender = _make_sender(sleeper)

        payload = _build_payload()
        delivered = await sender.send(WEBHOOK_URL, payload)

        assert delivered is True
        assert route.called
        assert delays == []

    @respx.mock
    async def test_request_body_contains_payload_json(self):
        """Тело запроса — JSON-представление payload со всеми полями уведомления."""
        delays, sleeper = _make_sleep_recorder()
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200))
        sender = _make_sender(sleeper)

        payload = _build_payload()
        await sender.send(WEBHOOK_URL, payload)

        sent_json = route.calls.last.request.read()
        import json

        body = json.loads(sent_json)
        assert body == payload.model_dump(mode="json")
        assert body["event"] == "payment.succeeded"
        assert body["status"] == "succeeded"

    @respx.mock
    async def test_retries_after_server_error_and_recovers(self):
        """500, затем 200: вторая попытка доставляет уведомление, пауза — 1с."""
        delays, sleeper = _make_sleep_recorder()
        route = respx.post(WEBHOOK_URL).mock(
            side_effect=[httpx.Response(500), httpx.Response(200)]
        )
        sender = _make_sender(sleeper)

        delivered = await sender.send(WEBHOOK_URL, _build_payload())

        assert delivered is True
        assert route.call_count == 2
        assert delays == [1.0]

    @respx.mock
    async def test_returns_false_after_all_attempts_exhausted(self):
        """Три 500 подряд: уведомление не доставлено, паузы растут 1с и 2с."""
        delays, sleeper = _make_sleep_recorder()
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(500))
        sender = _make_sender(sleeper)

        delivered = await sender.send(WEBHOOK_URL, _build_payload())

        assert delivered is False
        assert route.call_count == 3
        assert delays == [1.0, 2.0]

    @respx.mock
    async def test_client_error_is_retried_then_false(self):
        """Ответ 404 тоже считается ошибкой доставки: 3 попытки, затем False."""
        delays, sleeper = _make_sleep_recorder()
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(404))
        sender = _make_sender(sleeper)

        delivered = await sender.send(WEBHOOK_URL, _build_payload())

        assert delivered is False
        assert route.call_count == 3

    @respx.mock
    async def test_network_error_is_retried_and_does_not_raise(self):
        """Сетевой сбой (ConnectError) повторяется и не пробрасывается наружу."""
        delays, sleeper = _make_sleep_recorder()
        route = respx.post(WEBHOOK_URL).mock(
            side_effect=httpx.ConnectError("connection refused")
        )
        sender = _make_sender(sleeper)

        delivered = await sender.send(WEBHOOK_URL, _build_payload())

        assert delivered is False
        assert route.call_count == 3
        assert delays == [1.0, 2.0]


@pytest.mark.parametrize("status_code", [200, 201, 204, 299])
@respx.mock
async def test_any_2xx_counts_as_delivered(status_code: int):
    """Любой ответ 2xx считается успешной доставкой уведомления."""
    _, sleeper = _make_sleep_recorder()
    respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(status_code))
    sender = _make_sender(sleeper)
    assert await sender.send(WEBHOOK_URL, _build_payload()) is True
