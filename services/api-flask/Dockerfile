# Se construye desde la raíz del repositorio:
#   docker build -f services/api-flask/Dockerfile -t api-flask .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd --create-home --uid 10001 app

WORKDIR /app
COPY common/ /app/common/
COPY services/api-flask/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt /app/common

COPY services/api-flask/api_flask/ /app/api_flask/

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"

# 2 procesos por réplica; Container Apps agrega réplicas si hace falta.
CMD ["gunicorn", "--workers", "2", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "api_flask:crear_app()"]
