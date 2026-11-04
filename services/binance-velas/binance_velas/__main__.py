"""Punto de entrada: `python -m binance_velas`.

Variables de entorno:
  BINANCE_REST_BASE      https://api.binance.com
  BINANCE_SIMBOLOS       btcusdt,ethusdt,...
  VELAS_INTERVALOS       1h,1d
  MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD (o _FILE) / MYSQL_DATABASE / MYSQL_TLS
  NIVEL_LOG              INFO

Termina con código 0 si todo salió bien y 1 si algún símbolo falló
(Container Apps lo muestra como ejecución fallida).
"""

from __future__ import annotations

import sys
import time

import pymysql

from ingesta_common.config import config_mysql_desde_env, env_lista, env_str, kwargs_pymysql
from ingesta_common.logs import configurar_logs

from . import (FUENTE, MAX_VELAS, SQL_ULTIMA_VELA, SQL_UPSERT_VELAS, ErrorBinance, desde_para,
               parsear_velas, pedir_json, url_klines)


def actualizar(conn, base_rest: str, simbolos: list[str], intervalos: list[str], log,
               pedir=pedir_json) -> dict[str, int]:
    """Actualiza cada símbolo e intervalo. Devuelve {"SIMBOLO intervalo": velas guardadas, ...}."""
    resultado: dict[str, int] = {}
    errores = 0
    for simbolo in (s.upper() for s in simbolos):
        for intervalo in intervalos:
            clave = f"{simbolo} {intervalo}"
            try:
                with conn.cursor() as cur:
                    cur.execute(SQL_ULTIMA_VELA, (FUENTE, simbolo, intervalo))
                    (ultima,) = cur.fetchone()
                crudo = pedir(url_klines(base_rest, simbolo, intervalo, desde_para(ultima), MAX_VELAS))
                velas = parsear_velas(simbolo, intervalo, crudo)
                if velas:
                    with conn.cursor() as cur:
                        cur.executemany(SQL_UPSERT_VELAS, [v.fila() for v in velas])
                conn.commit()
                resultado[clave] = len(velas)
            except (ErrorBinance, pymysql.err.MySQLError) as e:
                conn.rollback()
                errores += 1
                resultado[clave] = -1
                log.error("No se pudo actualizar", extra={"datos": {"serie": clave, "motivo": str(e)[:300]}})
    if errores:
        log.warning("Terminó con errores", extra={"datos": {"errores": errores}})
    return resultado


def principal() -> int:
    log = configurar_logs("binance-velas", env_str("NIVEL_LOG", "INFO"))
    simbolos = env_lista("BINANCE_SIMBOLOS", "btcusdt,ethusdt,solusdt,bnbusdt,xrpusdt,adausdt")
    intervalos = env_lista("VELAS_INTERVALOS", "1h,1d")
    base_rest = env_str("BINANCE_REST_BASE", "https://api.binance.com")
    cfg = config_mysql_desde_env()
    inicio = time.monotonic()
    conn = pymysql.connect(**kwargs_pymysql(cfg["host"], cfg["puerto"], cfg["usuario"], cfg["clave"],
                                            cfg["base"], cfg["tls"], autocommit=False))
    try:
        resultado = actualizar(conn, base_rest, simbolos, intervalos, log)
    finally:
        conn.close()
    log.info("Velas actualizadas", extra={"datos": {"series": resultado,
                                                    "segundos": round(time.monotonic() - inicio, 1)}})
    return 1 if any(n < 0 for n in resultado.values()) else 0


if __name__ == "__main__":
    sys.exit(principal())
