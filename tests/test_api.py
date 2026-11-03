"""Pruebas de api-flask con un repositorio falso (sin MySQL)."""

from datetime import date, datetime
from decimal import Decimal

import pytest

from api_flask import crear_app


class RepoFalso:
    def __init__(self, caido=False):
        self.caido = caido
        self.llamadas = []

    def ping(self):
        if self.caido:
            raise ConnectionError("MySQL caído")

    def precios(self, fuente=None, simbolo=None):
        self.llamadas.append(("precios", fuente, simbolo))
        filas = [
            {"fuente": "binance", "simbolo": "BTCUSDT", "precio": Decimal("67000.010000000000"),
             "variacion_24h_pct": Decimal("1.2500"), "volumen_24h": Decimal("10.5"),
             "evento_ts": datetime(2026, 11, 2, 20, 0, 0, 123000)},
            {"fuente": "binance", "simbolo": "ETHUSDT", "precio": Decimal("2500.5"),
             "variacion_24h_pct": Decimal("-0.5"), "volumen_24h": None,
             "evento_ts": datetime(2026, 11, 2, 20, 0, 1)},
        ]
        return [f for f in filas if simbolo is None or f["simbolo"] == simbolo]

    def historicos(self, activo, moneda, intervalo, desde, limite):
        self.llamadas.append(("historicos", activo, moneda, intervalo, desde, limite))
        return []

    def incidentes(self, desde, limite):
        self.llamadas.append(("incidentes", desde, limite))
        return [{"fuente": "x", "id_externo": "1", "fecha": date(2026, 10, 1), "protocolo": "P",
                 "cadena": None, "tecnica": "flash loan", "monto_usd": Decimal("1000000.00"),
                 "url_referencia": None}]

    def eventos(self, limite):
        return []


@pytest.fixture
def repo():
    return RepoFalso()


@pytest.fixture
def cliente(repo, monkeypatch):
    monkeypatch.setenv("CORS_ORIGENES", "https://sitio.example")
    return crear_app(repo).test_client()


def test_precios_conserva_precision_y_usa_utc(cliente):
    r = cliente.get("/api/precios")
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo["total"] == 2
    btc = cuerpo["datos"][0]
    assert btc["precio"] == "67000.010000000000"          # texto, sin redondeo de coma flotante
    assert btc["evento_ts"] == "2026-11-02T20:00:00.123Z"
    assert cuerpo["datos"][1]["volumen_24h"] is None
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_precio_de_un_simbolo_y_404(cliente):
    assert cliente.get("/api/precios/btcusdt").get_json()["datos"][0]["simbolo"] == "BTCUSDT"
    r = cliente.get("/api/precios/DOGEUSDT")
    assert r.status_code == 404 and "DOGEUSDT" in r.get_json()["error"]


def test_historicos_valida_parametros(cliente, repo):
    assert cliente.get("/api/historicos").status_code == 400
    assert cliente.get("/api/historicos?activo=bitcoin&intervalo=2h").status_code == 400
    assert cliente.get("/api/historicos?activo=bitcoin&limite=5000").status_code == 400
    assert cliente.get("/api/historicos?activo=bitcoin&desde=ayer").status_code == 400
    r = cliente.get("/api/historicos?activo=Bitcoin&moneda=USD&intervalo=1h&desde=2026-10-01&limite=10")
    assert r.status_code == 200
    assert repo.llamadas[-1] == ("historicos", "bitcoin", "usd", "1h", datetime(2026, 10, 1), 10)


def test_incidentes_serializa_fecha_y_monto(cliente):
    dato = cliente.get("/api/incidentes?limite=5").get_json()["datos"][0]
    assert dato["fecha"] == "2026-10-01"
    assert dato["monto_usd"] == "1000000.00"


def test_cors_solo_para_origenes_permitidos(cliente):
    r = cliente.get("/api/precios", headers={"Origin": "https://sitio.example"})
    assert r.headers["Access-Control-Allow-Origin"] == "https://sitio.example"
    r = cliente.get("/api/precios", headers={"Origin": "https://malicioso.example"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_salud_y_preparacion(monkeypatch):
    app = crear_app(RepoFalso(caido=True)).test_client()
    assert app.get("/healthz").status_code == 200        # el proceso vive aunque MySQL no
    assert app.get("/readyz").status_code == 503         # pero no está listo para tráfico
    assert crear_app(RepoFalso()).test_client().get("/readyz").status_code == 200


def test_errores_en_json(cliente):
    r = cliente.get("/no-existe")
    assert r.status_code == 404 and r.get_json()["error"]
    r = cliente.post("/api/precios")
    assert r.status_code == 405 and r.get_json()["error"]


def test_error_interno_no_filtra_detalles(monkeypatch):
    class RepoRoto(RepoFalso):
        def precios(self, fuente=None, simbolo=None):
            raise RuntimeError("clave=secreta en el mensaje")
    r = crear_app(RepoRoto()).test_client().get("/api/precios")
    assert r.status_code == 500
    assert "secreta" not in r.get_data(as_text=True)
