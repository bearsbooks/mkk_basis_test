"""Конфигурация микросервиса (pydantic-settings).

Все параметры читаются из переменных окружения или файла .env,
что позволяет настраивать сервис без изменения кода (12-factor app).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Полный набор настроек сервиса: БД, брокер, ретраи, эмуляция шлюза."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Аутентификация -----------------------------------------------------
    # Статический API-ключ, ожидаемый в заголовке X-API-Key.
    api_key: str = "dev-api-key"

    # --- База данных ---------------------------------------------------------
    database_url: str = "postgresql+asyncpg://mkk:mkk@localhost:5432/mkk_payments"

    # --- Брокер сообщений ----------------------------------------------------
    rabbitmq_url: str = "amqp://mkk:mkk@localhost:5672/mkk"

    # Обменник и очереди RabbitMQ.
    payments_exchange: str = "payments"
    payments_queue: str = "payments.new"
    payments_dlq_queue: str = "payments.dlq"
    payments_dlx_exchange: str = "payments.dlx"

    # --- Outbox relay ---------------------------------------------------------
    # Интервал опроса outbox-таблицы (секунды) и размер пачки за один проход.
    outbox_poll_interval: float = 1.0
    outbox_batch_size: int = 100

    # --- Webhook ----------------------------------------------------------------
    # Максимальное число попыток доставки уведомления и база экспоненциальной задержки.
    webhook_max_attempts: int = 3
    webhook_backoff_base: float = 1.0
    webhook_timeout: float = 10.0

    # --- Retry потребителя -----------------------------------------------------
    consumer_max_attempts: int = 3
    consumer_backoff_base: float = 2.0

    # --- Эмуляция платёжного шлюза ---------------------------------------------
    gateway_min_delay: float = 2.0
    gateway_max_delay: float = 5.0
    gateway_success_rate: float = 0.9


@lru_cache
def get_settings() -> Settings:
    """Возвращает кэшированный singleton-экземпляр настроек.

    Кэширование через lru_cache гарантирует, что .env будет прочитан один раз,
    а все части приложения получат один и тот же объект настроек.
    """
    return Settings()
