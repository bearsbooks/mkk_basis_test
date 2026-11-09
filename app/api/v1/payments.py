"""Эндпоинты платежей: создание (202 Accepted) и получение детальной информации.

Все эндпоинты защищены статическим API-ключом (X-API-Key).
Создание платежа — асинхронная операция: запрос принимается (202),
дальнейшая обработка идёт через очередь сообщений.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import verify_api_key
from app.db.session import get_db
from app.schemas.payment import (
    PaymentCreate,
    PaymentCreatedResponse,
    PaymentResponse,
)
from app.services.payment_service import PaymentService

# Роутер версионированного API платежей; зависимость аутентификации — на весь роутер.
router = APIRouter(
    prefix="/api/v1/payments",
    tags=["payments"],
    dependencies=[Depends(verify_api_key)],
)

# Сокращённые псевдонимы типизированных зависимостей.
DbSession = Annotated[AsyncSession, Depends(get_db)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key")]


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PaymentCreatedResponse,
    summary="Создать платёж (асинхронная обработка)",
)
async def create_payment(
    body: PaymentCreate,
    idempotency_key: IdempotencyKey,
    session: DbSession,
) -> PaymentCreatedResponse:
    """Принимает платёж в обработку и возвращает 202 с идентификатором.

    Тело: сумма, валюта, описание, метаданные, webhook_url.
    Заголовок Idempotency-Key обязателен: повторный запрос с тем же ключом
    возвращает уже созданный платёж, не создавая дублей.
    """
    service = PaymentService(session)
    payment = await service.create_payment(body, idempotency_key=idempotency_key)
    return PaymentCreatedResponse(
        payment_id=payment.id,
        status=payment.status,
        created_at=payment.created_at,
    )


@router.get(
    "/{payment_id}",
    response_model=PaymentResponse,
    summary="Получить информацию о платеже",
)
async def get_payment(payment_id: uuid.UUID, session: DbSession) -> PaymentResponse:
    """Возвращает детальную информацию о платеже по его идентификатору."""
    service = PaymentService(session)
    payment = await service.get_payment(payment_id)
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Платёж {payment_id} не найден",
        )
    return PaymentResponse.model_validate(payment)
