"""Pydantic-схемы платежа: валидация входных данных, ответы API и webhook-payload.

Все схемы построены на Pydantic v2 и используются как на уровне API-эндпоинтов,
так и при формировании уведомлений для клиентов.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any

from pydantic import BaseModel, Field, HttpUrl, TypeAdapter, field_validator

# Переиспользуемый валидатор http(s)-адресов (создаётся один раз для производительности).
_HTTP_URL_ADAPTER: TypeAdapter[HttpUrl] = TypeAdapter(HttpUrl)

# Имена событий, отправляемых в webhook-уведомлениях.
PAYMENT_SUCCEEDED_EVENT: str = "payment.succeeded"
PAYMENT_FAILED_EVENT: str = "payment.failed"


class Currency(str, Enum):
    """Поддерживаемые валюты платежа: рубль, доллар, евро."""

    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"


class PaymentStatus(str, Enum):
    """Статусы жизненного цикла платежа.

    pending — платёж создан и ожидает обработки;
    succeeded — платёж успешно проведён шлюзом;
    failed — платёж отклонён шлюзом.
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# Общий тип денежной суммы: положительная, до 12 значащих цифр и 2 знаков после запятой.
Amount = Annotated[
    Decimal,
    Field(
        gt=0,
        max_digits=12,
        decimal_places=2,
        description="Сумма платежа (положительная, до двух знаков после запятой)",
    ),
]


class PaymentCreate(BaseModel):
    """Входные данные для создания платежа (тело POST /api/v1/payments)."""

    amount: Amount
    currency: Currency
    description: str = Field(min_length=1, max_length=255, description="Описание платежа")
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Произвольные метаданные платежа (JSON)",
    )
    webhook_url: str = Field(description="URL, на который придёт уведомление о результате")

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, value: str) -> str:
        """Проверяет, что webhook_url — корректный http(s)-адрес, и возвращает его как строку."""
        _HTTP_URL_ADAPTER.validate_python(value)
        return value


class PaymentCreatedResponse(BaseModel):
    """Тело ответа 202 Accepted на создание платежа."""

    payment_id: uuid.UUID
    status: PaymentStatus
    created_at: datetime


class PaymentResponse(BaseModel):
    """Детальная информация о платеже (ответ GET /api/v1/payments/{payment_id})."""

    model_config = {"from_attributes": True}

    id: uuid.UUID = Field(alias="payment_id")
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any]
    status: PaymentStatus
    idempotency_key: str
    webhook_url: str
    created_at: datetime
    processed_at: datetime | None = None


class WebhookPayload(BaseModel):
    """Уведомление о результате обработки платежа, отправляемое на webhook клиента."""

    event: str
    payment_id: uuid.UUID
    status: PaymentStatus
    amount: Decimal
    currency: Currency
    description: str
    processed_at: datetime
