# Единый образ для контейнеров api, consumer и migrate.
FROM python:3.12-slim

# Роль контейнера задаётся аргументом CMD: api | consumer | migrate.
WORKDIR /srv

# Сначала ставим зависимости отдельным слоем — кэшируется при неизменном requirements.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код приложения, миграции и точку входа.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["api"]
