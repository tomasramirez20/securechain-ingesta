"""Punto de entrada: `python -m binance_ws`.

Variables de entorno:
  BINANCE_WS_BASE        wss://stream.binance.com:9443
  BINANCE_SIMBOLOS       btcusdt,ethusdt,...
  MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD (o _FILE) / MYSQL_DATABASE
  MYSQL_TLS              true (Azure) / false (pruebas locales)
  FLUSH_S                segundos entre escrituras por lote (2)
  MAX_CONEXION_S         vida máxima de una conexión (82800 = 23 h)
  TIMEOUT_SILENCIO_S     segundos sin datos antes de reconectar (60)
  SALUD_PUERTO           puerto de /healthz (8080)
  SALUD_MAX_SILENCIO_S   segundos sin datos para responder 503 (90)
  NIVEL_LOG              INFO
"""

from __future__ import annotations

import asyncio
import signal

from ingesta_common.config import (config_mysql_desde_env, env_float, env_int, env_lista,
                                   env_str)
from ingesta_common.logs import configurar_logs
from ingesta_common.mysql_sink import MySQLSink
from ingesta_common.salud import EstadoSalud, servir_salud

from .cliente import BinanceTickerCliente
from .parser import Ticker, construir_url

FUENTE = "binance"

# Upsert idempotente: solo reemplaza la fila si el dato que llega es más reciente.
# MySQL evalúa las asignaciones en orden, por eso evento_ts va al final.
SQL_UPSERT_PRECIOS = """
INSERT INTO precios (fuente, simbolo, precio, variacion_24h_pct, volumen_24h, evento_ts)
VALUES (%s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
  precio            = IF(VALUES(evento_ts) >= evento_ts, VALUES(precio), precio),
  variacion_24h_pct = IF(VALUES(evento_ts) >= evento_ts, VALUES(variacion_24h_pct), variacion_24h_pct),
  volumen_24h       = IF(VALUES(evento_ts) >= evento_ts, VALUES(volumen_24h), volumen_24h),
  evento_ts         = GREATEST(evento_ts, VALUES(evento_ts))
"""


def a_fila(t: Ticker) -> tuple:
    # MySQL DATETIME no guarda zona horaria: se envía en UTC sin tzinfo.
    return (FUENTE, t.simbolo, t.precio, t.variacion_24h_pct, t.volumen_24h,
            t.evento_ts.replace(tzinfo=None))


async def principal() -> None:
    log = configurar_logs("binance-ws", env_str("NIVEL_LOG", "INFO"))
    simbolos = env_lista("BINANCE_SIMBOLOS", "btcusdt,ethusdt,solusdt,bnbusdt,xrpusdt,adausdt")
    url = construir_url(env_str("BINANCE_WS_BASE", "wss://stream.binance.com:9443"), simbolos)

    sink = MySQLSink(**config_mysql_desde_env(), logger=log)
    salud = EstadoSalud(max_silencio=env_float("SALUD_MAX_SILENCIO_S", 90))
    servidor = await servir_salud(salud, puerto=env_int("SALUD_PUERTO", 8080))

    async def guardar(tickers: list[Ticker]) -> None:
        await sink.guardar(SQL_UPSERT_PRECIOS, [a_fila(t) for t in tickers])

    cliente = BinanceTickerCliente(
        url, guardar, salud, log,
        max_conexion_s=env_float("MAX_CONEXION_S", 23 * 3600),
        flush_s=env_float("FLUSH_S", 2.0),
        timeout_silencio_s=env_float("TIMEOUT_SILENCIO_S", 60.0),
    )

    loop = asyncio.get_running_loop()
    for senal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(senal, cliente.parar)

    log.info("Iniciando binance-ws", extra={"datos": {"simbolos": simbolos}})
    try:
        await cliente.ejecutar()
    finally:
        servidor.close()
        await servidor.wait_closed()
        sink.cerrar()


if __name__ == "__main__":
    asyncio.run(principal())
