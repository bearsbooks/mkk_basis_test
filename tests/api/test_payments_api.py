"""API-тесты эндпоинтов /api/v1/payments: создание и получение платежа.

Проверяется контракт API: коды ответов, форма тела, идемпотентность,
аутентификация и фактические записи в базе данных.
"""

import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.models import OutboxMessage, Payment

# Тело валидного запроса на создание платежа.
CREATE_BODY = {
    "amount": "150.00",
    "currency": "RUB",
    "description": "Оплата заказа №42",
    "metadata": {"order_id": 42},
    "webhook_url": "https://client.example.com/webhooks",
}


class TestCreatePaymentEndpoint:
    """Проверки POST /api/v1/payments."""

    async def test_returns_202_with_payment_id_status_created_at(self, client, api_headers):
        """Успешное создание возвращает 202 и тело: payment_id, статус pending, created_at."""
        response = await client.post(
            "/api/v1/payments", json=CREATE_BODY, headers=api_headers
        )
        assert response.status_code == 202
        body = response.json()
        assert uuid.UUID(body["payment_id"])
        assert body["status"] == "pending"
        assert body["created_at"] is not None

    async def test_persists_payment_and_outbox_in_db(self, client, api_headers, db_session):
        """После 202-ответа платёж (pending) и outbox-событие лежат в БД."""
        response = await client.post(
            "/api/v1/payments", json=CREATE_BODY, headers=api_headers
        )
        payment_id = uuid.UUID(response.json()["payment_id"])

        payment = await db_session.get(Payment, payment_id)
        assert payment is not None
        assert payment.status.value == "pending"
        assert payment.amount == Decimal("150.00")
        assert payment.idempotency_key == "test-idem-key"

        outbox_count = await db_session.scalar(
            select(func.count()).select_from(OutboxMessage).where(
                OutboxMessage.aggregate_id == payment_id
            )
        )
        assert outbox_count == 1

    async def test_duplicate_idempotency_key_returns_same_payment(self, client, api_headers):
        """Повторный POST с тем же Idempotency-Key возвращает тот же payment_id."""
        first = await client.post("/api/v1/payments", json=CREATE_BODY, headers=api_headers)
        second = await client.post("/api/v1/payments", json=CREATE_BODY, headers=api_headers)
        assert first.status_code == second.status_code == 202
        assert first.json()["payment_id"] == second.json()["payment_id"]

    async def test_missing_idempotency_key_header_rejected(self, client):
        """Запрос без обязательного заголовка Idempotency-Key отклоняется (422)."""
        response = await client.post(
            "/api/v1/payments",
            json=CREATE_BODY,
            headers={"X-API-Key": "test-api-key"},
        )
        assert response.status_code == 422

    async def test_invalid_body_rejected_422(self, client, api_headers):
        """Тело с отрицательной суммой и неизвестной валютой отклоняется (422)."""
        response = await client.post(
            "/api/v1/payments",
            json={**CREATE_BODY, "amount": "-5", "currency": "GBP"},
            headers=api_headers,
        )
        assert response.status_code == 422

    async def test_missing_api_key_rejected_401(self, client):
        """Запрос без X-API-Key отклоняется на уровне эндпоинта (401)."""
        response = await client.post(
            "/api/v1/payments",
            json=CREATE_BODY,
            headers={"Idempotency-Key": "some-key"},
        )
        assert response.status_code == 401


class TestGetPaymentEndpoint:
    """Проверки GET /api/v1/payments/{payment_id}."""

    async def test_returns_full_payment_details(self, client, api_headers):
        """GET возвращает детальную информацию о созданном платеже."""
        created = await client.post(
            "/api/v1/payments", json=CREATE_BODY, headers=api_headers
        )
        payment_id = created.json()["payment_id"]

        response = await client.get(
            f"/api/v1/payments/{payment_id}",
            headers={"X-API-Key": "test-api-key"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["payment_id"] == payment_id
        assert body["status"] == "pending"
        assert body["amount"] == "150.00"
        assert body["currency"] == "RUB"
        assert body["description"] == "Оплата заказа №42"
        assert body["metadata"] == {"order_id": 42}
        assert body["idempotency_key"] == "test-idem-key"
        assert body["webhook_url"] == "https://client.example.com/webhooks"
        assert body["processed_at"] is None

    async def test_unknown_payment_returns_404(self, client):
        """GET несуществующего платежа возвращает 404."""
        response = await client.get(
            f"/api/v1/payments/{uuid.uuid4()}",
            headers={"X-API-Key": "test-api-key"},
        )
        assert response.status_code == 404
