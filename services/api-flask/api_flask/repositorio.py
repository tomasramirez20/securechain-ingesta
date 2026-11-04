"""Acceso de solo lectura a MySQL para la API.

La API usa un usuario de MySQL que solo tiene permiso SELECT: aunque alguien
lograra inyectar algo en una petición, no podría modificar datos. Además todas
las consultas usan parámetros (%s), nunca texto concatenado.
"""

from __future__ import annotations

from datetime import date, datetime

import pymysql
import pymysql.cursors

from ingesta_common.config import kwargs_pymysql


class Repositorio:
    def __init__(self, host: str, puerto: int, usuario: str, clave: str, base: str, tls: bool = True):
        self._params = kwargs_pymysql(host, puerto, usuario, clave, base, tls,
                                      autocommit=True, cursorclass=pymysql.cursors.DictCursor)

    def _consultar(self, sql: str, params: tuple = ()) -> list[dict]:
        # Una conexión por petición: simple y suficiente para el tráfico esperado.
        # Container Apps escala réplicas si el tráfico crece.
        conn = pymysql.connect(**self._params)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
        finally:
            conn.close()

    def ping(self) -> None:
        self._consultar("SELECT 1 AS ok")

    def precios(self, fuente: str | None = None, simbolo: str | None = None) -> list[dict]:
        sql = ("SELECT fuente, simbolo, precio, variacion_24h_pct, volumen_24h, evento_ts "
               "FROM precios WHERE 1=1")
        params: list = []
        if fuente:
            sql += " AND fuente = %s"
            params.append(fuente)
        if simbolo:
            sql += " AND simbolo = %s"
            params.append(simbolo)
        sql += " ORDER BY simbolo, fuente"
        return self._consultar(sql, tuple(params))

    def historicos(self, activo: str, moneda: str, intervalo: str,
                   desde: datetime | None, limite: int) -> list[dict]:
        sql = ("SELECT fuente, activo, moneda_cotizacion, intervalo, ts, precio, capitalizacion, volumen "
               "FROM historicos WHERE activo = %s AND moneda_cotizacion = %s AND intervalo = %s")
        params: list = [activo, moneda, intervalo]
        if desde:
            sql += " AND ts >= %s"
            params.append(desde)
        sql += " ORDER BY ts DESC LIMIT %s"
        params.append(limite)
        return self._consultar(sql, tuple(params))

    def incidentes(self, desde: date | None, limite: int) -> list[dict]:
        sql = ("SELECT fuente, id_externo, fecha, protocolo, cadena, tecnica, monto_usd, url_referencia "
               "FROM incidentes")
        params: list = []
        if desde:
            sql += " WHERE fecha >= %s"
            params.append(desde)
        sql += " ORDER BY fecha DESC, monto_usd DESC LIMIT %s"
        params.append(limite)
        return self._consultar(sql, tuple(params))

    def velas(self, intervalo: str, desde: datetime, simbolo: str | None = None) -> list[dict]:
        sql = ("SELECT simbolo, apertura_ts, apertura, maximo, minimo, cierre, volumen "
               "FROM velas WHERE fuente = 'binance' AND intervalo = %s AND apertura_ts >= %s")
        params: list = [intervalo, desde]
        if simbolo:
            sql += " AND simbolo = %s"
            params.append(simbolo)
        sql += " ORDER BY simbolo, apertura_ts"
        return self._consultar(sql, tuple(params))

    def eventos(self, limite: int) -> list[dict]:
        return self._consultar(
            "SELECT fuente, id_activo, id_mercado, pregunta, precio, evento_ts "
            "FROM eventos ORDER BY evento_ts DESC LIMIT %s", (limite,))
