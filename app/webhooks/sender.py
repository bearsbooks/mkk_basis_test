"""Отправка webhook-уведомлений о результате обработки платежа.

Клиент указывает webhook_url при создании платежа; после завершения
обработки сервис доставляет уведомление с повторными попытками
(3 попытки, экспоненциальная задержка). Метод send никогда не бросает
исключений: недоставка — возвращаемое False, а не сбой потребителя.
"""

import asyncio
from typing import Any, Callable, Awaitable

import httpx

from app.schemas.payment import WebhookPayload
from app.utils.retry import retry_with_backoff


class WebhookSender:
    """Доставляет WebhookPayload на URL клиента с ретраями."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        max_attempts: int = 3,
        backoff_base: float = 1.0,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Сохраняет HTTP-клиент, параметры повторов и подменяемый sleep."""
        self._client = client
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._sleeper = sleeper

    async def send(self, url: str, payload: WebhookPayload) -> bool:
        """POST-ит payload как JSON; True при 2xx, False после исчерпания попыток.

        Успехом считается любой ответ 2xx. Серверные (5xx), клиентские (4xx)
        ошибки и сетевые сбои (таймауты, отказ соединения) повторяются
        с экспоненциальной задержкой.
        """
        body: dict[str, Any] = payload.model_dump(mode="json")

        async def _attempt() -> None:
            """Одна попытка доставки: POST и raise_for_status при любом не-2xx."""
            response = await self._client.post(url, json=body)
            response.raise_for_status()

        try:
            await retry_with_backoff(
                _attempt,
                attempts=self._max_attempts,
                base_delay=self._backoff_base,
                exceptions=(httpx.HTTPError,),
                sleeper=self._sleeper,
            )
        except httpx.HTTPError:
            # Все попытки исчерпаны — фиксируем недоставку и продолжаем работу.
            return False
        return True
