"""Юнит-тесты Pydantic-схем платежа.

Проверяют валидацию входных данных создания платежа,
перечисления статусов и сериализацию payload для webhook.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.payment import (
    Currency,
    PaymentCreate,
    PaymentStatus,
    WebhookPayload,
)


class TestPaymentCreate:
    """Тесты схемы создания платежа (PaymentCreate)."""

    def test_valid_payload_is_accepted(self):
        """Полностью валидные данные проходят валидацию без ошибок."""
        payment = PaymentCreate(
            amount=Decimal("100.50"),
            currency="RUB",
            description="Оплата заказа №42",
            metadata={"order_id": 42},
            webhook_url="https://client.example.com/webhooks",
        )
        assert payment.amount == Decimal("100.50")
        assert payment.currency is Currency.RUB
        assert payment.webhook_url == "https://client.example.com/webhooks"

    @pytest.mark.parametrize("bad_amount", [Decimal("0"), Decimal("-10.00")])
    def test_zero_and_negative_amount_are_rejected(self, bad_amount):
        """Нулевая и отрицательная сумма отклоняются с ошибкой валидации."""
        with pytest.raises(ValidationError):
            PaymentCreate(
                amount=bad_amount,
                currency="RUB",
                description="desc",
                webhook_url="https://client.example.com/hook",
            )

    def test_more_than_two_decimal_places_are_rejected(self):
        """Сумма с тремя знаками после запятой отклоняется (максимум — два)."""
        with pytest.raises(ValidationError):
            PaymentCreate(
                amount=Decimal("10.999"),
                currency="USD",
                description="desc",
                webhook_url="https://client.example.com/hook",
            )

    @pytest.mark.parametrize("currency", ["RUB", "USD", "EUR"])
    def test_supported_currencies_are_accepted(self, currency):
        """Валюты RUB, USD и EUR проходят валидацию."""
        payment = PaymentCreate(
            amount=Decimal("1.00"),
            currency=currency,
            description="desc",
            webhook_url="https://client.example.com/hook",
        )
        assert payment.currency.value == currency

    def test_unknown_currency_is_rejected(self):
        """Неподдерживаемая валюта (GBP) отклоняется с ошибкой валидации."""
        with pytest.raises(ValidationError):
            PaymentCreate(
                amount=Decimal("1.00"),
                currency="GBP",
                description="desc",
                webhook_url="https://client.example.com/hook",
            )

    def test_metadata_is_optional_and_defaults_to_empty_dict(self):
        """Метаданные необязательны: по умолчанию — пустой словарь."""
        payment = PaymentCreate(
            amount=Decimal("1.00"),
            currency="RUB",
            description="desc",
            webhook_url="https://client.example.com/hook",
        )
        assert payment.metadata == {}

    @pytest.mark.parametrize(
        "bad_url", ["ftp://client.example.com/hook", "not-a-url", ""]
    )
    def test_non_http_webhook_url_is_rejected(self, bad_url):
        """Webhook URL, не являющийся корректным http(s)-адресом, отклоняется."""
        with pytest.raises(ValidationError):
            PaymentCreate(
                amount=Decimal("1.00"),
                currency="RUB",
                description="desc",
                webhook_url=bad_url,
            )

    def test_missing_required_fields_are_rejected(self):
        """Отсутствие обязательных полей (сумма, валюта, webhook) — ошибка."""
        with pytest.raises(ValidationError):
            PaymentCreate(description="нет суммы и валюты")


class TestEnums:
    """Тесты перечислений статусов и валют."""

    def test_payment_status_members(self):
        """Статус платежа принимает ровно три значения: pending/succeeded/failed."""
        assert {s.value for s in PaymentStatus} == {"pending", "succeeded", "failed"}

    def test_currency_members(self):
        """Валюта принимает ровно три значения: RUB/USD/EUR."""
        assert {c.value for c in Currency} == {"RUB", "USD", "EUR"}


class TestWebhookPayload:
    """Тесты схемы уведомления (payload), отправляемого на webhook клиента."""

    def test_payload_holds_all_notification_fields(self):
        """Payload содержит все поля, нужные клиенту для идентификации платежа."""
        payload = WebhookPayload(
            event="payment.succeeded",
            payment_id="0b6b6f8e-4a1c-4d0f-9d3e-7f2c1a0b5d44",
            status="succeeded",
            amount=Decimal("150.00"),
            currency="RUB",
            description="Оплата заказа №42",
            processed_at="2026-11-09T12:00:00+00:00",
        )
        data = payload.model_dump(mode="json")
        assert data["event"] == "payment.succeeded"
        assert data["payment_id"] == "0b6b6f8e-4a1c-4d0f-9d3e-7f2c1a0b5d44"
        assert data["status"] == "succeeded"
        assert data["currency"] == "RUB"
        assert "amount" in data and "processed_at" in data
