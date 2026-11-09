"""Эмуляция внешнего платёжного шлюза.

По заданию обработка занимает 2–5 секунд и завершается успехом
в 90% случаев; в 10% случаев шлюз возвращает отказ.
Задержка, вероятность и sleep инъектируются — это делает логику
полностью детерминированной в тестах.
"""

import asyncio
import random
import uuid
from typing import Any, Protocol


class PaymentGatewayError(Exception):
    """Отказ внешнего платёжного шлюза (платёж не проведён)."""


class RandomLike(Protocol):
    """Минимальный интерфейс ГПСЧ, необходимый эмулятору."""

    def uniform(self, low: float, high: float) -> float:
        """Возвращает случайное число из диапазона [low, high]."""
        ...

    def random(self) -> float:
        """Возвращает случайное число из диапазона [0.0, 1.0)."""
        ...


async def _default_sleeper(seconds: float) -> None:
    """Реальное асинхронное ожидание (используется вне тестов)."""
    await asyncio.sleep(seconds)


class PaymentGatewayEmulator:
    """Эмулятор обработки платежа: пауза 2–5 с и вероятностный исход."""

    def __init__(
        self,
        min_delay: float = 2.0,
        max_delay: float = 5.0,
        success_rate: float = 0.9,
        rng: RandomLike | None = None,
        sleeper: Any = _default_sleeper,
    ) -> None:
        """Сохраняет параметры эмуляции и коллабораторов (ГПСЧ, sleep)."""
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._success_rate = success_rate
        self._rng = rng if rng is not None else random.Random()
        self._sleeper = sleeper

    async def process_payment(self, payment_id: uuid.UUID) -> None:
        """Эмулирует обращение к шлюзу за 2–5 секунд.

        При успешном исходе метод просто возвращается; при отказе —
        бросает PaymentGatewayError (это бизнес-исход, а не системный сбой).
        """
        delay = self._rng.uniform(self._min_delay, self._max_delay)
        await self._sleeper(delay)
        if self._rng.random() >= self._success_rate:
            raise PaymentGatewayError(f"Шлюз отклонил платёж {payment_id}")
