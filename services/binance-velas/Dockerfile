# Se construye desde la raíz del repositorio:
#   docker build -f services/binance-velas/Dockerfile -t binance-velas .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home --uid 10001 app

WORKDIR /app
COPY common/ /app/common/
COPY services/binance-velas/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt /app/common

COPY services/binance-velas/binance_velas/ /app/binance_velas/

USER app
# Trabajo programado: arranca, actualiza las velas y termina. No escucha ningún puerto.
CMD ["python", "-m", "binance_velas"]
