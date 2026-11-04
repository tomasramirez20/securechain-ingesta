"""Pruebas del trabajo binance-velas sin red ni MySQL."""

import json
import logging
import urllib.error
from datetime import datetime
from decimal import Decimal

import pymysql
import pytest

import binance_velas
from binance_velas import ErrorBinance, desde_para, parsear_velas, pedir_json, url_klines
from binance_velas.__main__ import actualizar

KLINE = [1762250400000, "70000.10", "70500.00", "69900.00", "70400.25", "12.5",
         1762253999999, "880000", 1500, "6.1", "430000", "0"]


def test_parsea_kline_de_binance_sin_perder_precision():
    (v,) = parsear_velas("BTCUSDT", "1h", [KLINE])
    assert v.apertura_ts == datetime(2025, 11, 4, 10, 0, 0)
    assert v.apertura == Decimal("70000.10") and v.cierre == Decimal("70400.25")
    assert v.fila()[:4] == ("binance", "BTCUSDT", "1h", datetime(2025, 11, 4, 10, 0, 0))


def test_respuestas_raras_son_error_claro():
    with pytest.raises(ErrorBinance):
        parsear_velas("BTCUSDT", "1h", {"code": -1121, "msg": "Invalid symbol."})
    with pytest.raises(ErrorBinance):
        parsear_velas("BTCUSDT", "1h", [[1, "no-numero"]])


def test_url_incremental():
    assert url_klines("https://api.binance.com/", "BTCUSDT", "1d", None, 1000) == \
        "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1d&limit=1000"
    desde = desde_para(datetime(2025, 11, 4, 10))
    assert desde == 1762250400000
    assert f"startTime={desde}" in url_klines("https://x", "BTCUSDT", "1h", desde, 1000)
    assert desde_para(None) is None


def test_451_no_se_reintenta(monkeypatch):
    llamadas = []

    def falso(req, timeout):
        llamadas.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 451, "Unavailable", {}, None)

    monkeypatch.setattr(binance_velas.urllib.request, "urlopen", falso)
    with pytest.raises(ErrorBinance, match="451"):
        pedir_json("https://api.binance.com/api/v3/klines", espera=0)
    assert len(llamadas) == 1


def test_errores_temporales_se_reintentan(monkeypatch):
    intentos = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps([KLINE]).encode()

    def falso(req, timeout):
        intentos.append(1)
        if len(intentos) < 3:
            raise urllib.error.HTTPError(req.full_url, 503, "Busy", {}, None)
        return Resp()

    monkeypatch.setattr(binance_velas.urllib.request, "urlopen", falso)
    assert pedir_json("https://x", espera=0) == [KLINE]
    assert len(intentos) == 3


class CursorFalso:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.conn.consultas.append(params)

    def fetchone(self):
        return (self.conn.ultima,)

    def executemany(self, sql, filas):
        if self.conn.falla_escritura:
            raise pymysql.err.OperationalError(1142, "INSERT denied")
        self.conn.escritas.extend(filas)


class ConnFalsa:
    def __init__(self, ultima=None, falla_escritura=False):
        self.ultima = ultima
        self.falla_escritura = falla_escritura
        self.consultas, self.escritas = [], []
        self.commits = self.rollbacks = 0

    def cursor(self):
        return CursorFalso(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_actualizar_guarda_cada_serie_y_pide_desde_la_ultima():
    conn = ConnFalsa(ultima=datetime(2025, 11, 4, 10))
    urls = []

    def pedir(url):
        urls.append(url)
        return [KLINE]

    r = actualizar(conn, "https://api.binance.com", ["btcusdt", "ethusdt"], ["1h", "1d"],
                   logging.getLogger("t"), pedir=pedir)
    assert r == {"BTCUSDT 1h": 1, "BTCUSDT 1d": 1, "ETHUSDT 1h": 1, "ETHUSDT 1d": 1}
    assert all("startTime=1762250400000" in u for u in urls)
    assert len(conn.escritas) == 4 and conn.commits == 4


def test_una_serie_que_falla_no_frena_las_demas():
    conn = ConnFalsa()

    def pedir(url):
        if "ETHUSDT" in url:
            raise ErrorBinance("caído")
        return [KLINE]

    r = actualizar(conn, "https://x", ["btcusdt", "ethusdt"], ["1h"], logging.getLogger("t"), pedir=pedir)
    assert r == {"BTCUSDT 1h": 1, "ETHUSDT 1h": -1}
    assert conn.rollbacks == 1
