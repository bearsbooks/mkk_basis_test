"""ORM-модель outbox-таблицы для гарантированной публикации событий.

Outbox pattern: событие записывается в ту же транзакцию, что и платёж,
а фоновый процесс (relay) публикует его в RabbitMQ и помечает время публикации.
Так достигается гарантия «платёж создан ⟺ событие обязательно уйдёт в брокер».
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OutboxMessage(Base):
    """Запись исходящего события: тело сообщения и отметка о публикации в брокер."""

    __tablename__ = "outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    def __repr__(self) -> str:
        """Возвращает краткое представление outbox-записи для логов."""
        return f"<OutboxMessage id={self.id} type={self.event_type} published={self.published_at is not None}>"
