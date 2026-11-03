import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from binance_ws.parser import construir_url, parsear_mensaje


def ticker_binance(simbolo="BTCUSDT", precio="67000.01", evento_ms=1_700_000_000_000):
    return {"e": "24hrTicker", "E": evento_ms, "s": simbolo, "c": precio,
            "P": "1.234", "v": "12345.6", "p": "800.0", "q": "999"}


def test_mensaje_combinado_valido():
    t = parsear_mensaje(json.dumps({"stream": "btcusdt@ticker", "data": ticker_binance()}))
    assert t is not None
    assert t.simbolo == "BTCUSDT"
    assert t.precio == Decimal("67000.01")
    assert t.variacion_24h_pct == Decimal("1.234")
    assert t.evento_ts == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


def test_mensaje_sin_envoltorio_tambien_sirve():
    assert parsear_mensaje(json.dumps(ticker_binance())).simbolo == "BTCUSDT"


@pytest.mark.parametrize("texto", [
    "no es json",
    json.dumps({"result": None, "id": 1}),
    json.dumps({"stream": "x", "data": {"e": "trade"}}),
    json.dumps({"data": {**ticker_binance(), "c": "abc"}}),
    json.dumps({"data": {k: v for k, v in ticker_binance().items() if k != "E"}}),
    json.dumps([1, 2, 3]),
])
def test_mensajes_invalidos_devuelven_none(texto):
    assert parsear_mensaje(texto) is None


def test_construir_url():
    url = construir_url("wss://stream.binance.com:9443/", ["BTCUSDT", " ethusdt "])
    assert url == "wss://stream.binance.com:9443/stream?streams=btcusdt@ticker/ethusdt@ticker"


def test_construir_url_valida_limites():
    with pytest.raises(ValueError):
        construir_url("wss://x", [])
    with pytest.raises(ValueError):
        construir_url("wss://x", [f"s{i}" for i in range(1025)])
