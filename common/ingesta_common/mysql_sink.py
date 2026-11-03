"""Escritura por lotes en MySQL con una reconexión automática.

PyMySQL es bloqueante: cada lote corre en un hilo aparte (asyncio.to_thread)
para no frenar la lectura del WebSocket. Los lotes van uno a la vez, así que
basta una sola conexión.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

import pymysql

from .config import kwargs_pymysql


class MySQLSink:
    def __init__(self, host: str, puerto: int, usuario: str, clave: str, base: str,
                 tls: bool = True, logger: logging.Logger | None = None):
        self._params = kwargs_pymysql(host, puerto, usuario, clave, base, tls, autocommit=False)
        self._conn: pymysql.connections.Connection | None = None
        self._log = logger or logging.getLogger("mysql-sink")

    def _conectar(self) -> None:
        self._conn = pymysql.connect(**self._params)
        self._log.info("Conectado a MySQL", extra={"datos": {"host": self._params["host"],
                                                              "base": self._params.get("database")}})

    def _cerrar(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def ejecutar_lote(self, sql: str, filas: Sequence[Sequence]) -> int:
        """Ejecuta `sql` una vez por fila en una sola transacción. Devuelve las filas enviadas."""
        if not filas:
            return 0
        for intento in (1, 2):
            try:
                if self._conn is None:
                    self._conectar()
                with self._conn.cursor() as cur:
                    cur.executemany(sql, filas)
                self._conn.commit()
                return len(filas)
            except (pymysql.err.OperationalError, pymysql.err.InterfaceError):
                # Conexión caída (reinicio o mantenimiento de MySQL): reconectar una vez.
                self._cerrar()
                if intento == 2:
                    raise
        return 0  # inalcanzable

    async def guardar(self, sql: str, filas: Sequence[Sequence]) -> int:
        return await asyncio.to_thread(self.ejecutar_lote, sql, filas)

    def cerrar(self) -> None:
        self._cerrar()
