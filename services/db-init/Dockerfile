# Se construye desde la raíz del repositorio:
#   docker build -f services/db-init/Dockerfile -t db-init .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home --uid 10001 app

WORKDIR /app
COPY common/ /app/common/
RUN pip install "PyMySQL>=1.1,<2" /app/common

COPY services/db-init/db_init/ /app/db_init/
COPY db/schema.sql /app/db_init/schema.sql

USER app
CMD ["python", "-m", "db_init"]
