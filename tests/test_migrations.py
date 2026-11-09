"""Тесты миграций Alembic: структура таблиц payments и outbox.

Миграции применяются к реальному PostgreSQL (testcontainers), поэтому
эти тесты проверяют фактическую схему базы, а не только ORM-модели.
"""

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine


async def _fetch_schema(engine: AsyncEngine, table: str) -> dict[str, Any]:
    """Собирает колонки и индексы таблицы, пока соединение открыто.

    Интроспекция выполняется внутри run_sync: Inspector вне контекста
    соединения работать не может (соединение закрывается после async with).
    """

    def _collect(sync_conn: Any) -> dict[str, Any]:
        """Читает схему таблицы через Inspector на синхронном соединении."""
        inspector = sa.inspect(sync_conn)
        return {
            "columns": {col["name"] for col in inspector.get_columns(table)},
            "indexes": inspector.get_indexes(table),
        }

    async with engine.connect() as conn:
        return await conn.run_sync(_collect)


class TestMigrations:
    """Проверка схемы, созданной миграцией Alembic."""

    async def test_payments_and_outbox_tables_exist(self, db_engine):
        """Миграция создаёт обе таблицы: payments и outbox."""
        async with db_engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: sa.inspect(sync_conn).get_table_names()
            )
        assert "payments" in tables
        assert "outbox" in tables

    async def test_payments_columns(self, db_engine):
        """Таблица payments содержит все поля сущности «Платеж» из задания."""
        schema = await _fetch_schema(db_engine, "payments")
        assert schema["columns"] == {
            "id",
            "amount",
            "currency",
            "description",
            "metadata",
            "status",
            "idempotency_key",
            "webhook_url",
            "created_at",
            "processed_at",
        }

    async def test_outbox_columns(self, db_engine):
        """Таблица outbox содержит поля для гарантированной публикации событий."""
        schema = await _fetch_schema(db_engine, "outbox")
        assert schema["columns"] == {
            "id",
            "event_type",
            "payload",
            "aggregate_id",
            "created_at",
            "published_at",
        }

    async def test_idempotency_key_has_unique_index(self, db_engine):
        """На колонке idempotency_key существует уникальный индекс (защита от дублей)."""
        schema = await _fetch_schema(db_engine, "payments")
        idempotency_indexes = [
            idx for idx in schema["indexes"] if idx["column_names"] == ["idempotency_key"]
        ]
        assert idempotency_indexes, "индекс по idempotency_key не найден"
        assert all(idx.get("unique") for idx in idempotency_indexes)

    async def test_outbox_published_at_indexed(self, db_engine):
        """Колонка published_at в outbox проиндексирована (для быстрого опроса поллером)."""
        schema = await _fetch_schema(db_engine, "outbox")
        indexed_columns = {
            col for idx in schema["indexes"] for col in idx["column_names"]
        }
        assert "published_at" in indexed_columns
