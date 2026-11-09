"""Outbox-relay: фоновый процесс гарантированной публикации событий в RabbitMQ.

Выбирает неопубликованные записи из таблицы outbox (FOR UPDATE SKIP LOCKED —
несколько инстансов API не конфликтуют), публикует их в брокер и помечает
published_at. Публикация и пометка выполняются в одной транзакции:
если брокер упал — запись остаётся неопубликованной и уйдёт в следующем
проходе; возможный дубль публикации безопасен благодаря идемпотентности
consumer-а (терминальный статус платежа пропускается).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import OutboxMessage

logger = logging.getLogger(__name__)

# Тип функции публикации: получает payload события и его тип (routing key).
Publisher = Callable[[dict[str, Any], str], Awaitable[None]]


class OutboxRelay:
    """Фоновый публикатор outbox-событий с пакетной обработкой."""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        publisher: Publisher,
        batch_size: int = 100,
    ) -> None:
        """Сохраняет фабрику сессий, функцию публикации и размер пачки."""
        self._session_factory = session_factory
        self._publisher = publisher
        self._batch_size = batch_size

    async def publish_pending(self) -> int:
        """Выполняет один проход: публикует неопубликованные события.

        Возвращает число опубликованных событий. При сбое брокера
        пробрасывает исключение (записи остаются pending), а run_forever
        логирует его и повторяет проход через интервал.
        """
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(OutboxMessage)
                        .where(OutboxMessage.published_at.is_(None))
                        .order_by(OutboxMessage.created_at)
                        .limit(self._batch_size)
                        # SKIP LOCKED: параллельные инстансы не блокируют друг друга.
                        .with_for_update(skip_locked=True)
                    )
                )
                .scalars()
                .all()
            )

            published_at = datetime.now(timezone.utc)
            for row in rows:
                await self._publisher(row.payload, row.event_type)
                row.published_at = published_at
            await session.commit()
            return len(rows)

    async def run_forever(self, interval: float) -> None:
        """Бесконечный цикл опроса outbox-таблицы с заданным интервалом.

        Одиночные сбои (брокер/БД) логируются и не останавливают цикл;
        корректно завершается по CancelledError (остановка приложения).
        """
        while True:
            try:
                await self.publish_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Проход outbox-relay завершился ошибкой, повторю позже")
            await asyncio.sleep(interval)
