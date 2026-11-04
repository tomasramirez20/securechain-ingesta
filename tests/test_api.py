"""Pruebas de api-flask con un repositorio falso (sin MySQL)."""

from datetime import date, datetime, timezone
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

    def velas(self, intervalo, desde, simbolo=None):
        self.llamadas.append(("velas", intervalo, desde, simbolo))
        filas = [
            {"simbolo": "BTCUSDT", "apertura_ts": datetime(2026, 11, 4, 10), "apertura": Decimal("70000"),
             "maximo": Decimal("70500.5"), "minimo": Decimal("69900"), "cierre": Decimal("70400.25"),
             "volumen": Decimal("12.5")},
            {"simbolo": "ETHUSDT", "apertura_ts": datetime(2026, 11, 4, 10), "apertura": Decimal("2300"),
             "maximo": Decimal("2350"), "minimo": Decimal("2290"), "cierre": Decimal("2340"),
             "volumen": Decimal("100")},
        ]
        return [f for f in filas if simbolo is None or f["simbolo"] == simbolo]


class RegistroFalso:
    def __init__(self):
        self.guardadas = []

    def guardar(self, solicitud):
        self.guardadas.append(solicitud)
        return "SC-ABC1234"


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


# ---------------------------------------------------------------- velas

def test_velas_agrupa_por_simbolo_en_formato_compacto(cliente, repo):
    r = cliente.get("/api/velas?intervalo=1h")
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo["intervalo"] == "1h"
    assert set(cuerpo["series"]) == {"BTCUSDT", "ETHUSDT"}
    ms = int(datetime(2026, 11, 4, 10, tzinfo=timezone.utc).timestamp() * 1000)
    assert cuerpo["series"]["BTCUSDT"][0] == [ms, 70000.0, 70500.5, 69900.0, 70400.25, 12.5]
    assert "max-age=60" in r.headers["Cache-Control"]
    _, intervalo, desde, simbolo = repo.llamadas[-1]
    assert intervalo == "1h" and simbolo is None
    # Por defecto, 24 velas de 1 h: desde hace ~24 h.
    horas = (datetime.now(timezone.utc).replace(tzinfo=None) - desde).total_seconds() / 3600
    assert 23.9 < horas < 24.1


def test_velas_filtra_simbolo_y_valida(cliente, repo):
    r = cliente.get("/api/velas?intervalo=1d&simbolo=ethusdt&limite=30")
    assert list(r.get_json()["series"]) == ["ETHUSDT"]
    assert repo.llamadas[-1][3] == "ETHUSDT"
    assert cliente.get("/api/velas?intervalo=5m").status_code == 400
    assert cliente.get("/api/velas?simbolo=BTC;DROP").status_code == 400
    assert cliente.get("/api/velas?limite=5000").status_code == 400


# ---------------------------------------------------------------- solicitudes del Sello

SOLICITUD = {
    "empresa": "Exchange Andino S.A.S.", "sitio_web": "https://exchange-andino.co", "tipo": "exchange",
    "pais": "Colombia", "contacto": "Ana Pérez", "cargo": "Cumplimiento", "correo": "ana@exchange-andino.co",
    "interes": "sello", "mensaje": "Queremos iniciar el proceso.", "acepta": True,
}


@pytest.fixture
def con_registro(repo, monkeypatch):
    from api_flask.registro import LimitadorTasa
    monkeypatch.setenv("CORS_ORIGENES", "https://sitio.example")
    reg = RegistroFalso()
    app = crear_app(repo, registro=reg, limitador=LimitadorTasa(max_por_ventana=2, ventana_s=600))
    return app.test_client(), reg


def test_solicitud_valida_se_guarda_y_devuelve_referencia(con_registro):
    cliente, reg = con_registro
    r = cliente.post("/api/solicitudes", json=SOLICITUD)
    assert r.status_code == 201
    assert r.get_json() == {"referencia": "SC-ABC1234"}
    assert reg.guardadas[0]["empresa"] == "Exchange Andino S.A.S."
    assert "acepta" not in reg.guardadas[0]


def test_solicitud_invalida_explica_cada_campo(con_registro):
    cliente, reg = con_registro
    malo = dict(SOLICITUD, correo="no-es-correo", tipo="banco", empresa="  ", acepta=False)
    r = cliente.post("/api/solicitudes", json=malo)
    assert r.status_code == 400
    campos = r.get_json()["campos"]
    assert set(campos) == {"correo", "tipo", "empresa", "acepta"}
    assert reg.guardadas == []


def test_solicitud_campo_trampa_no_guarda(con_registro):
    cliente, reg = con_registro
    r = cliente.post("/api/solicitudes", json=dict(SOLICITUD, fax="soy un bot"))
    assert r.status_code == 201
    assert reg.guardadas == []


def test_solicitud_limita_envios_por_cliente(con_registro):
    cliente, _ = con_registro
    cabecera = {"X-Forwarded-For": "203.0.113.7"}
    assert cliente.post("/api/solicitudes", json=SOLICITUD, headers=cabecera).status_code == 201
    assert cliente.post("/api/solicitudes", json=SOLICITUD, headers=cabecera).status_code == 201
    assert cliente.post("/api/solicitudes", json=SOLICITUD, headers=cabecera).status_code == 429
    # Otro cliente no se ve afectado.
    assert cliente.post("/api/solicitudes", json=SOLICITUD,
                        headers={"X-Forwarded-For": "198.51.100.2"}).status_code == 201


def test_solicitud_rechaza_cuerpos_grandes_y_no_json(con_registro):
    cliente, _ = con_registro
    assert cliente.post("/api/solicitudes", data="x" * 20000,
                        content_type="application/json").status_code == 413
    assert cliente.post("/api/solicitudes", data="hola", content_type="text/plain").status_code == 400


def test_formulario_apagado_sin_credenciales(cliente, monkeypatch):
    monkeypatch.delenv("MYSQL_REGISTRO_USER", raising=False)
    assert cliente.post("/api/solicitudes", json=SOLICITUD).status_code == 503
