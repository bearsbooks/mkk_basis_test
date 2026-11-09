"""Переиспользуемый хелпер повторных попыток с экспоненциальной задержкой.

Задержка перед попыткой N: base_delay * 2^(N-1), то есть при base=1с
паузы равны 1с, 2с, 4с... Используется и потребителем сообщений,
 и отправителем webhook-уведомлений.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


async def retry_with_backoff(
    func: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Выполняет await func() до `attempts` раз с экспоненциальной задержкой.

    Повторяет только исключения из `exceptions`; «посторонние» пробрасываются
    сразу. Если все попытки исчерпаны — пробрасывает последнее исключение
    (вызывающий сам решает, что делать: nack в DLQ, залогировать и т.д.).
    """
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await func()
        except exceptions as error:  # noqa: PERF203 — retry по определению ловит в цикле
            last_error = error
            if attempt < attempts:
                await sleeper(base_delay * 2 ** (attempt - 1))
    # Все попытки исчерпаны: здесь last_error гарантированно установлен.
    raise last_error  # type: ignore[misc]
