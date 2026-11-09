"""Реестр ORM-моделей проекта.

Импорт моделей здесь обязателен: Alembic строит метаданные таблиц
по Base.metadata, поэтому все модели должны быть загружены к моменту
чтения target_metadata в alembic/env.py.
"""

from app.models.base import Base
from app.models.outbox import OutboxMessage
from app.models.payment import Payment

__all__ = ["Base", "Payment", "OutboxMessage"]
