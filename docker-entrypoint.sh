#!/bin/sh
# Точка входа контейнера: выбирает роль по первому аргументу.
#   api      — uvicorn (HTTP API + фоновый outbox-relay)
#   consumer — faststream run (обработчик очереди payments.new)
#   migrate  — разовое применение миграций Alembic
set -e

ROLE="${1:-api}"

if [ "$ROLE" = "migrate" ]; then
    echo "Применение миграций Alembic..."
    exec alembic upgrade head
fi

if [ "$ROLE" = "api" ]; then
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
fi

if [ "$ROLE" = "consumer" ]; then
    exec faststream run app.consumers.payments:broker
fi

# Произвольная команда (например, для отладки): sh.
exec "$@"
