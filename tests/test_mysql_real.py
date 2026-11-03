"""Pruebas contra un MySQL/MariaDB real. Se saltan si no hay MYSQL_TEST_HOST.

En GitHub Actions corren contra el servicio mysql:8.4 del workflow.
Recorren el camino completo: db-init prepara la base y los usuarios,
la ingesta escribe con el usuario `ingesta` y la API lee con `api_lectura`.
"""

import logging
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pymysql
import pytest

from api_flask.repositorio import Repositorio
from binance_ws.__main__ import SQL_UPSERT_PRECIOS
from db_init import preparar
from ingesta_common.mysql_sink import MySQLSink

pytestmark = pytest.mark.skipif(not os.environ.get("MYSQL_TEST_HOST"),
                                reason="Sin MYSQL_TEST_HOST: no hay base de datos de prueba")

ESQUEMA = (Path(__file__).resolve().parents[1] / "db" / "schema.sql").read_text(encoding="utf-8")
BASE = os.environ.get("MYSQL_TEST_DATABASE", "securechain_test")
CLAVES = {"ingesta": "clave-ingesta-prueba", "api_lectura": "clave-api-prueba"}


def conexion_admin(**extra):
    return pymysql.connect(host=os.environ["MYSQL_TEST_HOST"], port=int(os.environ.get("MYSQL_TEST_PORT", 3306)),
                           user=os.environ["MYSQL_TEST_ADMIN_USER"], password=os.environ["MYSQL_TEST_ADMIN_PASSWORD"],
                           **extra)


def params(usuario):
    return dict(host=os.environ["MYSQL_TEST_HOST"], puerto=int(os.environ.get("MYSQL_TEST_PORT", 3306)),
                usuario=usuario, clave=CLAVES[usuario], base=BASE, tls=False)


@pytest.fixture(scope="module")
def base_preparada():
    conn = conexion_admin()
    try:
        preparar(conn, BASE, ESQUEMA, CLAVES, logging.getLogger("prueba"))
        preparar(conn, BASE, ESQUEMA, CLAVES, logging.getLogger("prueba"))   # idempotente
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM `{BASE}`.precios")
        conn.commit()
    finally:
        conn.close()


def fila(precio, segundo):
    return ("binance", "BTCUSDT", Decimal(precio), Decimal("1.5"), Decimal("10"),
            datetime(2026, 11, 2, 12, 0, segundo))


async def test_ingesta_escribe_y_api_lee(base_preparada):
    sink = MySQLSink(**params("ingesta"))
    repo = Repositorio(**params("api_lectura"))
    try:
        await sink.guardar(SQL_UPSERT_PRECIOS, [fila("100.5", 10)])
        await sink.guardar(SQL_UPSERT_PRECIOS, [fila("100.5", 10)])       # repetido: no duplica
        assert len(repo.precios()) == 1
        await sink.guardar(SQL_UPSERT_PRECIOS, [fila("101.25", 20)])      # más nuevo: reemplaza
        await sink.guardar(SQL_UPSERT_PRECIOS, [fila("99.0", 15)])        # más viejo: se ignora
        p = repo.precios(simbolo="BTCUSDT")[0]
        assert p["precio"] == Decimal("101.25")
        assert p["evento_ts"] == datetime(2026, 11, 2, 12, 0, 20)
        repo.ping()
    finally:
        sink.cerrar()


def test_api_lectura_no_puede_escribir(base_preparada):
    p = params("api_lectura")
    conn = pymysql.connect(host=p["host"], port=p["puerto"], user=p["usuario"], password=p["clave"], database=BASE)
    try:
        with conn.cursor() as cur, pytest.raises(pymysql.err.OperationalError):
            cur.execute("DELETE FROM precios")
    finally:
        conn.close()


def test_ingesta_no_puede_borrar(base_preparada):
    p = params("ingesta")
    conn = pymysql.connect(host=p["host"], port=p["puerto"], user=p["usuario"], password=p["clave"], database=BASE)
    try:
        with conn.cursor() as cur, pytest.raises(pymysql.err.OperationalError):
            cur.execute("DROP TABLE precios")
    finally:
        conn.close()


async def test_sink_reconecta_si_mysql_cierra_la_conexion(base_preparada):
    sink = MySQLSink(**params("ingesta"))
    try:
        await sink.guardar(SQL_UPSERT_PRECIOS, [fila("100", 30)])
        admin = conexion_admin(autocommit=True)
        with admin.cursor() as cur:
            cur.execute("SELECT ID FROM information_schema.PROCESSLIST WHERE USER = 'ingesta'")
            for (pid,) in cur.fetchall():
                cur.execute(f"KILL {int(pid)}")
        admin.close()
        await sink.guardar(SQL_UPSERT_PRECIOS, [fila("200", 40)])
        assert Repositorio(**params("api_lectura")).precios(simbolo="BTCUSDT")[0]["precio"] == Decimal("200")
    finally:
        sink.cerrar()
