"""Асинхронное окружение Alembic.

URL подключения выбирается в порядке приоритета:
1. аргумент CLI `-x db_url=...` (так миграции запускаются в тестах);
2. переменная окружения DATABASE_URL (прод/локальный запуск);
3. значение sqlalchemy.url из alembic.ini (последний резерв).
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Импорт реестра моделей: загружает все таблицы в Base.metadata.
from app.models import Base

# Объект конфигурации Alembic (доступ к ini-файлу и аргументам CLI).
config = context.config

# Настройка логирования по ini-файлу, если он доступен.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Подстановка URL подключения (см. докстринг модуля).
x_url = context.get_x_argument(as_dictionary=True).get("db_url")
if x_url:
    config.set_main_option("sqlalchemy.url", x_url)
elif os.getenv("DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])

# Метаданные моделей — источник истины для autogenerate.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Запускает миграции в offline-режиме: генерирует SQL-скрипт без БД."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Выполняет миграции в транзакции на уже открытом соединении."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Создаёт асинхронный движок (asyncpg) и выполняет миграции через него."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Точка входа онлайн-миграций: делегирует работу асинхронному движку."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
