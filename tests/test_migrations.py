"""Тесты миграций Alembic: структура таблиц payments и outbox.

Миграции применяются к реальному PostgreSQL (testcontainers), поэтому
эти тесты проверяют фактическую схему базы, а не только ORM-модели.
"""

from sqlalchemy.engine import Inspector
from sqlalchemy.ext.asyncio import AsyncEngine


async def _inspect(engine: AsyncEngine, table: str) -> Inspector:
    """Возвращает интроспекцию указанной таблицы через асинхронное подключение."""
    async with engine.connect() as conn:
        return await conn.run_sync(lambda sync_conn: Inspector.from_engine(sync_conn))


class TestMigrations:
    """Проверка схемы, созданной миграцией Alembic."""

    async def test_payments_and_outbox_tables_exist(self, db_engine):
        """Миграция создаёт обе таблицы: payments и outbox."""
        async with db_engine.connect() as conn:
            tables = await conn.run_sync(
                lambda sync_conn: Inspector.from_engine(sync_conn).get_table_names()
            )
        assert "payments" in tables
        assert "outbox" in tables

    async def test_payments_columns(self, db_engine):
        """Таблица payments содержит все поля сущности «Платеж» из задания."""
        inspector = await _inspect(db_engine, "payments")
        columns = {col["name"] for col in inspector.get_columns("payments")}
        assert columns == {
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
        inspector = await _inspect(db_engine, "outbox")
        columns = {col["name"] for col in inspector.get_columns("outbox")}
        assert columns == {
            "id",
            "event_type",
            "payload",
            "aggregate_id",
            "created_at",
            "published_at",
        }

    async def test_idempotency_key_has_unique_index(self, db_engine):
        """На колонке idempotency_key существует уникальный индекс (защита от дублей)."""
        inspector = await _inspect(db_engine, "payments")
        indexes = inspector.get_indexes("payments")
        idempotency_indexes = [
            idx for idx in indexes if idx["column_names"] == ["idempotency_key"]
        ]
        assert idempotency_indexes, "индекс по idempotency_key не найден"
        assert all(idx.get("unique") for idx in idempotency_indexes)

    async def test_outbox_published_at_indexed(self, db_engine):
        """Колонка published_at в outbox проиндексирована (для быстрого опроса поллером)."""
        inspector = await _inspect(db_engine, "outbox")
        indexed_columns = {
            col for idx in inspector.get_indexes("outbox") for col in idx["column_names"]
        }
        assert "published_at" in indexed_columns
