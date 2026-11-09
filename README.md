# Асинхронный сервис процессинга платежей

Микросервис для асинхронной обработки платежей: принимает запросы на оплату,
обрабатывает их через эмуляцию внешнего платёжного шлюза (2–5 секунд, 90% успех /
10% отказ) и уведомляет клиента о результате через webhook.

Проект разработан **строго по TDD** (история коммитов: сначала тест — красная
фаза, затем реализация — зелёная). Все функции задокументированы русскими
docstring-комментариями.

## Стек

| Компонент | Технология |
|---|---|
| API | FastAPI + Pydantic v2 |
| ORM / БД | SQLAlchemy 2.0 (async) + PostgreSQL |
| Миграции | Alembic (async) |
| Брокер | RabbitMQ + FastStream |
| Тесты | pytest, pytest-asyncio, pytest-cov, respx, testcontainers (PostgreSQL, RabbitMQ) |
| Инфраструктура | Docker + docker-compose |

## Архитектура

```mermaid
flowchart LR
    C[Клиент] -->|POST /api/v1/payments\nX-API-Key, Idempotency-Key| API[API\nFastAPI]
    API -->|одна транзакция| PG[(PostgreSQL\npayments + outbox)]
    API -->|202 Accepted| C
    R[Outbox relay\nфоновая задача API] -->|SELECT ... FOR UPDATE SKIP LOCKED| PG
    R -->|publish payments.new| RMQ[[RabbitMQ\npayments.new]]
    RMQ --> CON[Consumer\nFastStream]
    CON -->|эмуляция шлюза 2-5с| GW[Платёжный шлюз\n90% успех / 10% отказ]
    CON -->|статус + processed_at| PG
    CON -->|webhook + retry ×3| C
    RMQ -.->|nack после 3 неудач| DLQ[[payments.dlq]]
    C2[Клиент] -->|GET /api/v1/payments/{id}| API
```

Поток обработки:

1. **Создание платежа** — `POST /api/v1/payments` валидирует запрос и в одной
   транзакции записывает платёж (статус `pending`) и событие `payments.new`
   в таблицу `outbox` (**outbox pattern** — гарантия «платёж создан ⟺ событие
   обязательно уйдёт в брокер»). Клиенту возвращается `202 Accepted`.
2. **Outbox relay** — фоновая задача API-процесса опрашивает таблицу `outbox`
   (`SELECT ... FOR UPDATE SKIP LOCKED`), публикует неопубликованные события
   в RabbitMQ и проставляет `published_at`. Сбой брокера не теряет события —
   они уйдут в следующем проходе.
3. **Consumer** — единственный обработчик очереди `payments.new`: эмулирует
   шлюз, обновляет статус (`succeeded`/`failed`) и `processed_at` в БД,
   отправляет webhook-уведомление с повторными попытками.
4. **Гарантии** — повторные попытки с экспоненциальной задержкой (3 попытки:
   1с → 2с), после исчерпания сообщение отклоняется (nack) и RabbitMQ
   переносит его в **Dead Letter Queue** `payments.dlq`.

### Идемпотентность

- **Создание**: уникальный индекс на `idempotency_key`. Повторный `POST`
  с тем же заголовком `Idempotency-Key` возвращает уже созданный платёж,
  не создавая дублей (в т.ч. при гонке запросов — через `IntegrityError`).
- **Consumer**: повторная доставка сообщения для платежа в терминальном
  статусе безопасно пропускается (webhook не дублируется).

## Запуск (docker-compose)

```bash
cp .env.example .env
docker compose up --build
```

Поднимаются сервисы:

| Сервис | Назначение | Порт |
|---|---|---|
| `postgres` | PostgreSQL 16 | 5432 |
| `rabbitmq` | RabbitMQ 3.13 (+ Management UI) | 5672, [15672](http://localhost:15672) (mkk/mkk) |
| `migrate` | разовое применение миграций Alembic | — |
| `api` | HTTP API + outbox relay | [8000](http://localhost:8000/docs) |
| `consumer` | обработчик очереди `payments.new` | — |

Swagger UI: http://localhost:8000/docs

## Примеры

### Создание платежа

```bash
curl -X POST http://localhost:8000/api/v1/payments \
  -H "Content-Type: application/json" \
  -H "X-API-Key: dev-api-key" \
  -H "Idempotency-Key: order-42-payment-1" \
  -d '{
    "amount": "150.00",
    "currency": "RUB",
    "description": "Оплата заказа №42",
    "metadata": {"order_id": 42},
    "webhook_url": "https://webhook.site/your-uuid"
  }'
```

Ответ `202 Accepted`:

```json
{
  "payment_id": "3f8d2b1e-9c4a-4d67-a1b2-c3d4e5f6a7b8",
  "status": "pending",
  "created_at": "2026-11-09T12:00:00.123456+00:00"
}
```

Повторный запрос с тем же `Idempotency-Key` вернёт тот же `payment_id`.

### Получение информации о платеже

```bash
curl http://localhost:8000/api/v1/payments/3f8d2b1e-9c4a-4d67-a1b2-c3d4e5f6a7b8 \
  -H "X-API-Key: dev-api-key"
```

Ответ `200 OK` (спустя 2–5 секунд после создания статус станет терминальным):

```json
{
  "payment_id": "3f8d2b1e-9c4a-4d67-a1b2-c3d4e5f6a7b8",
  "amount": "150.00",
  "currency": "RUB",
  "description": "Оплата заказа №42",
  "metadata": {"order_id": 42},
  "status": "succeeded",
  "idempotency_key": "order-42-payment-1",
  "webhook_url": "https://webhook.site/your-uuid",
  "created_at": "2026-11-09T12:00:00.123456+00:00",
  "processed_at": "2026-11-09T12:00:03.654321+00:00"
}
```

### Webhook-уведомление

На `webhook_url` придёт POST (3 попытки с задержками 1с/2с при ошибках):

```json
{
  "event": "payment.succeeded",
  "payment_id": "3f8d2b1e-9c4a-4d67-a1b2-c3d4e5f6a7b8",
  "status": "succeeded",
  "amount": "150.00",
  "currency": "RUB",
  "description": "Оплата заказа №42",
  "processed_at": "2026-11-09T12:00:03.654321+00:00"
}
```

Для отказа шлюза `event` = `payment.failed`, `status` = `failed`.

### Локальный webhook-приёмник для проверки

```bash
python -m http.server 9999
# и укажите webhook_url = http://host.docker.internal:9999/hook
```

### DLQ

Сообщения, упавшие после 3 повторных попыток, попадают в очередь
`payments.dlq` — их видно в RabbitMQ Management UI (http://localhost:15672,
очередь `payments.dlq`).

## Тесты

Тесты поднимают эфемерные PostgreSQL 16 и RabbitMQ 3.13 в Docker
(testcontainers) автоматически — нужен только запущенный Docker Desktop.

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"   # Windows (Linux/macOS: .venv/bin/pip)
.venv/Scripts/python -m pytest          # быстрый набор (PostgreSQL в Docker)
.venv/Scripts/python -m pytest -m e2e   # + e2e с реальным RabbitMQ (DLQ)
```

Покрытие:

```bash
.venv/Scripts/python -m pytest --cov=app --cov-report=term-missing
```

Структура тестов (TDD — каждый модуль писался тест-первым):

```
tests/
├── conftest.py                  # PostgreSQL-контейнер, alembic upgrade, сессии
├── test_migrations.py           # схема payments/outbox, уникальный индекс
├── unit/
│   ├── test_schemas.py          # валидация Pydantic-схем
│   ├── test_auth.py             # аутентификация X-API-Key
│   ├── test_gateway.py          # эмулятор шлюза (детерминированный)
│   ├── test_retry.py            # retry с экспоненциальной задержкой
│   └── test_webhook_sender.py   # доставка webhook + retry (respx)
├── api/
│   ├── conftest.py              # приложение с тестовыми зависимостями
│   └── test_payments_api.py     # POST/GET контракт, идемпотентность, 401/404/422
├── services/
│   ├── test_payment_service.py  # платёж + outbox в одной транзакции
│   └── test_outbox_relay.py     # публикация, SKIP LOCKED, батчи, устойчивость
├── consumers/
│   ├── test_payment_processor.py    # шлюз → статус → webhook, идемпотентность
│   └── test_payments_consumer.py    # полный путь через TestRabbitBroker, retry×3
└── e2e/
    └── test_dlq_e2e.py          # реальный RabbitMQ: 3 неудачи → payments.dlq
```

## Структура проекта

```
app/
├── main.py                  # фабрика приложения + lifespan (брокер, outbox relay)
├── config.py                # pydantic-settings (всё через переменные окружения)
├── api/
│   ├── deps.py              # аутентификация X-API-Key (compare_digest)
│   └── v1/payments.py       # POST/GET /api/v1/payments
├── db/session.py            # async engine + sessionmaker + зависимость get_db
├── models/                  # ORM: Payment (payments), OutboxMessage (outbox)
├── schemas/                 # Pydantic v2: PaymentCreate/Response, WebhookPayload, сообщения
├── services/
│   ├── payment_service.py   # создание платежа + outbox (одна транзакция), идемпотентность
│   ├── payment_processor.py # единый обработчик: шлюз → статус → webhook
│   └── outbox_relay.py      # фоновый публикатор outbox-событий
├── gateway/emulator.py      # эмуляция шлюза: 2–5с, 90% успех
├── webhooks/sender.py       # webhook с retry (3 попытки, экспоненциально)
├── consumers/payments.py    # FastStream: топология (DLX/DLQ), подписчики
└── utils/retry.py           # переиспользуемый retry_with_backoff
alembic/                     # async-миграции (0001: payments, outbox)
```

## Переменные окружения

| Переменная | По умолчанию | Описание |
|---|---|---|
| `API_KEY` | `dev-api-key` | статический ключ заголовка `X-API-Key` |
| `DATABASE_URL` | `postgresql+asyncpg://mkk:mkk@localhost:5432/mkk_payments` | подключение к PostgreSQL |
| `RABBITMQ_URL` | `amqp://mkk:mkk@localhost:5672/` | подключение к RabbitMQ |
| `OUTBOX_POLL_INTERVAL` | `1.0` | интервал опроса outbox (с) |
| `WEBHOOK_MAX_ATTEMPTS` | `3` | попытки доставки webhook |
| `CONSUMER_MAX_ATTEMPTS` | `3` | повторные попытки обработки сообщения |
| `GATEWAY_MIN_DELAY` / `GATEWAY_MAX_DELAY` | `2.0` / `5.0` | границы задержки шлюза (с) |
| `GATEWAY_SUCCESS_RATE` | `0.9` | вероятность успеха шлюза |
