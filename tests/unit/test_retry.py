"""Юнит-тесты хелпера повторных попыток с экспоненциальной задержкой.

Задержка между попытками растёт как base * 2^(номер-попытки-1):
например, при base=1.0 паузы равны 1с, 2с, 4с и т.д.
"""

import pytest

from app.utils.retry import retry_with_backoff


def _make_sleep_recorder() -> tuple[list[float], object]:
    """Создаёт подмену asyncio.sleep, записывающую длительности пауз."""

    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        """Сохраняет запрошенную длительность паузы вместо реального ожидания."""
        recorded.append(seconds)

    return recorded, fake_sleep


class TestRetryWithBackoff:
    """Проверки retry_with_backoff."""

    async def test_returns_result_on_first_success(self):
        """Успех с первой попытки: без пауз и без повторных вызовов."""
        delays, sleeper = _make_sleep_recorder()
        calls = []

        async def flaky() -> str:
            """Функция, всегда завершающаяся успешно."""
            calls.append(1)
            return "ok"

        result = await retry_with_backoff(flaky, attempts=3, base_delay=1.0, sleeper=sleeper)
        assert result == "ok"
        assert calls == [1]
        assert delays == []

    async def test_retries_with_exponential_delays_until_success(self):
        """Две ошибки, затем успех: паузы растут экспоненциально (1с, 2с)."""
        delays, sleeper = _make_sleep_recorder()
        attempts = []

        async def flaky() -> str:
            """Функция, падающая дважды и завершающаяся успехом на третий раз."""
            attempts.append(1)
            if len(attempts) < 3:
                raise ValueError("временная ошибка")
            return "recovered"

        result = await retry_with_backoff(flaky, attempts=3, base_delay=1.0, sleeper=sleeper)
        assert result == "recovered"
        assert len(attempts) == 3
        assert delays == [1.0, 2.0]

    async def test_raises_last_error_after_all_attempts_fail(self):
        """После исчерпания попыток пробрасывается последнее исключение."""
        delays, sleeper = _make_sleep_recorder()
        calls = []

        async def always_fails() -> None:
            """Функция, всегда падающая."""
            calls.append(1)
            raise RuntimeError(f"сбой №{len(calls)}")

        with pytest.raises(RuntimeError, match="сбой №3"):
            await retry_with_backoff(
                always_fails, attempts=3, base_delay=0.5, sleeper=sleeper
            )
        assert len(calls) == 3
        assert delays == [0.5, 1.0]

    async def test_unexpected_exception_type_is_not_retried(self):
        """Исключения вне списка exceptions пробрасываются сразу, без повторов."""
        delays, sleeper = _make_sleep_recorder()
        calls = []

        async def wrong_error() -> None:
            """Функция, падающая «посторонним» исключением (TypeError)."""
            calls.append(1)
            raise TypeError("не тот тип ошибки")

        with pytest.raises(TypeError):
            await retry_with_backoff(
                wrong_error,
                attempts=3,
                base_delay=1.0,
                sleeper=sleeper,
                exceptions=(ValueError,),
            )
        assert len(calls) == 1
        assert delays == []
