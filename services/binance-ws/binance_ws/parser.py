"""Traducción de mensajes de Binance a objetos de SecureChain.

Stream combinado `/stream?streams=btcusdt@ticker/ethusdt@ticker`; cada mensaje:
    {"stream": "btcusdt@ticker", "data": {"e": "24hrTicker", "E": 1700000000000,
     "s": "BTCUSDT", "c": "67000.01", "P": "1.234", "v": "12345.6", ...}}
E = hora del evento (ms), s = símbolo, c = último precio,
P = variación 24 h en %, v = volumen 24 h del activo base.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

MAX_STREAMS_POR_CONEXION = 1024  # límite documentado por Binance


@dataclass(frozen=True)
class Ticker:
    simbolo: str
    precio: Decimal
    variacion_24h_pct: Decimal
    volumen_24h: Decimal
    evento_ts: datetime  # UTC, con zona horaria


def construir_url(base: str, simbolos: list[str]) -> str:
    if not simbolos:
        raise ValueError("Se necesita al menos un símbolo (p. ej. btcusdt)")
    if len(simbolos) > MAX_STREAMS_POR_CONEXION:
        raise ValueError(f"Binance permite máximo {MAX_STREAMS_POR_CONEXION} streams por conexión")
    streams = "/".join(f"{s.strip().lower()}@ticker" for s in simbolos)
    return f"{base.rstrip('/')}/stream?streams={streams}"


def parsear_mensaje(texto: str | bytes) -> Ticker | None:
    """Devuelve un Ticker, o None si el mensaje no es un ticker válido."""
    try:
        mensaje = json.loads(texto)
    except (ValueError, TypeError):
        return None
    if not isinstance(mensaje, dict):
        return None
    datos = mensaje.get("data", mensaje)
    if not isinstance(datos, dict) or datos.get("e") != "24hrTicker":
        return None
    try:
        return Ticker(
            simbolo=str(datos["s"]).upper(),
            precio=Decimal(datos["c"]),
            variacion_24h_pct=Decimal(datos["P"]),
            volumen_24h=Decimal(datos["v"]),
            evento_ts=datetime.fromtimestamp(int(datos["E"]) / 1000, tz=timezone.utc),
        )
    except (KeyError, TypeError, ValueError, ArithmeticError, OverflowError, OSError):
        return None
