"""Юнит-тесты эмулятора внешнего платёжного шлюза.

Эмулятор должен имитировать обработку 2–5 секунд с вероятностью успеха 90%.
Для детерминированности в тесты инъектируются подделки ГПСЧ и sleep.
"""

import uuid

import pytest

from app.gateway.emulator import PaymentGatewayEmulator, PaymentGatewayError


class FakeRng:
    """Подмена random.Random: возвращает заранее заданные значения."""

    def __init__(self, uniform_value: float = 3.0, random_value: float = 0.0) -> None:
        """Запоминает значения, которые вернут uniform и random."""
        self.uniform_value = uniform_value
        self.random_value = random_value
        self.uniform_args: tuple[float, float] | None = None

    def uniform(self, low: float, high: float) -> float:
        """Фиксирует границы задержки и возвращает заданное значение."""
        self.uniform_args = (low, high)
        return self.uniform_value

    def random(self) -> float:
        """Возвращает заданное значение для проверки вероятности успеха."""
        return self.random_value


def _make_sleep_recorder() -> tuple[list[float], object]:
    """Создаёт подмену asyncio.sleep, записывающую длительности пауз."""

    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        """Сохраняет запрошенную длительность паузы вместо реального ожидания."""
        recorded.append(seconds)

    return recorded, fake_sleep


class TestPaymentGatewayEmulator:
    """Проверки эмуляции обработки платежа."""

    async def test_successful_processing_does_not_raise(self):
        """При «удачной» случайности обработка завершается без исключения."""
        _, sleeper = _make_sleep_recorder()
        gateway = PaymentGatewayEmulator(rng=FakeRng(random_value=0.0), sleeper=sleeper)
        # Не должно бросить исключение: random()=0.0 < success_rate=0.9.
        await gateway.process_payment(uuid.uuid4())

    async def test_failed_processing_raises_gateway_error(self):
        """При «неудачной» случайности бросается PaymentGatewayError."""
        _, sleeper = _make_sleep_recorder()
        gateway = PaymentGatewayEmulator(rng=FakeRng(random_value=0.95), sleeper=sleeper)
        with pytest.raises(PaymentGatewayError):
            await gateway.process_payment(uuid.uuid4())

    async def test_delay_is_taken_from_configured_bounds(self):
        """Задержка берётся из диапазона [min_delay, max_delay] через rng.uniform."""
        delays, sleeper = _make_sleep_recorder()
        rng = FakeRng(uniform_value=3.0)
        gateway = PaymentGatewayEmulator(
            min_delay=2.0, max_delay=5.0, rng=rng, sleeper=sleeper
        )
        await gateway.process_payment(uuid.uuid4())
        assert rng.uniform_args == (2.0, 5.0)
        assert delays == [3.0]

    async def test_default_bounds_match_assignment(self):
        """По умолчанию границы задержки — 2 и 5 секунд, вероятность успеха — 0.9."""
        _, sleeper = _make_sleep_recorder()
        rng = FakeRng(uniform_value=4.9, random_value=0.89)
        gateway = PaymentGatewayEmulator(rng=rng, sleeper=sleeper)
        await gateway.process_payment(uuid.uuid4())
        assert rng.uniform_args == (2.0, 5.0)

    async def test_custom_success_rate_boundary(self):
        """Значение random() ровно на границе success_rate считается отказом."""
        _, sleeper = _make_sleep_recorder()
        gateway = PaymentGatewayEmulator(
            success_rate=0.9, rng=FakeRng(random_value=0.9), sleeper=sleeper
        )
        with pytest.raises(PaymentGatewayError):
            await gateway.process_payment(uuid.uuid4())
