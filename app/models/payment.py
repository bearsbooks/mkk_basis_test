"""ORM-модель сущности «Платеж» (таблица payments)."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import DateTime, Enum as SAEnum, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.schemas.payment import Currency, PaymentStatus


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    """Возвращает значения enum-класса для хранения в колонке БД.

    SQLAlchemy по умолчанию хранит имена членов (PENDING), а заданию нужны
    значения в нижнем регистре ("pending"), поэтому подменяем колбэк значений.
    """
    return [member.value for member in enum_cls]


class Payment(Base):
    """Сущность «Платеж»: полный жизненный цикл операции оплаты.

    Атрибут metadata_ намеренно назван с подчёркиванием: имя `metadata`
    конфликтует со служебным атрибутом DeclarativeBase, но в БД колонка
    называется ровно `metadata`, как в задании.
    """

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[Currency] = mapped_column(
        SAEnum(Currency, native_enum=False, values_callable=_enum_values, length=3),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    status: Mapped[PaymentStatus] = mapped_column(
        SAEnum(PaymentStatus, native_enum=False, values_callable=_enum_values, length=16),
        nullable=False,
        default=PaymentStatus.PENDING,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    webhook_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        """Возвращает краткое представление платежа для отладочных логов."""
        return f"<Payment id={self.id} status={self.status} amount={self.amount}>"
