"""binance-velas: trabajo programado que guarda el histórico de precios (velas OHLCV) de Binance.

Es la pieza serverless de SecureChain: un trabajo de Container Apps que arranca
según un horario, pide a la API REST pública de Binance las velas que faltan,
las guarda en MySQL y termina. Entre ejecuciones no hay nada encendido.

  - Primera ejecución: carga el histórico completo (hasta 1000 velas por símbolo e intervalo).
  - Siguientes: solo pide desde la última vela guardada (carga incremental).
  - La vela en curso se reescribe en cada ejecución hasta que cierra (escritura idempotente).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

FUENTE = "binance"
DURACION_MS = {"1h": 3_600_000, "1d": 86_400_000}
MAX_VELAS = 1000  # tope de Binance por petición

SQL_UPSERT_VELAS = """
INSERT INTO velas (fuente, simbolo, intervalo, apertura_ts, apertura, maximo, minimo, cierre, volumen)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
  maximo = VALUES(maximo), minimo = VALUES(minimo), cierre = VALUES(cierre), volumen = VALUES(volumen)
"""

SQL_ULTIMA_VELA = ("SELECT MAX(apertura_ts) FROM velas "
                   "WHERE fuente = %s AND simbolo = %s AND intervalo = %s")


class ErrorBinance(RuntimeError):
    """Binance respondió con error o con datos que no se entienden."""


@dataclass(frozen=True)
class Vela:
    simbolo: str
    intervalo: str
    apertura_ts: datetime  # UTC, sin tzinfo (así lo guarda MySQL)
    apertura: Decimal
    maximo: Decimal
    minimo: Decimal
    cierre: Decimal
    volumen: Decimal

    def fila(self) -> tuple:
        return (FUENTE, self.simbolo, self.intervalo, self.apertura_ts, self.apertura,
                self.maximo, self.minimo, self.cierre, self.volumen)


def parsear_velas(simbolo: str, intervalo: str, crudo) -> list[Vela]:
    """Convierte la respuesta de /api/v3/klines en velas.

    Cada elemento es [apertura_ms, apertura, maximo, minimo, cierre, volumen, cierre_ms, ...].
    Los precios llegan como texto y se convierten a Decimal para no perder precisión.
    """
    if not isinstance(crudo, list):
        raise ErrorBinance(f"Respuesta inesperada para {simbolo} {intervalo}: {str(crudo)[:200]}")
    velas = []
    for k in crudo:
        try:
            velas.append(Vela(
                simbolo=simbolo, intervalo=intervalo,
                apertura_ts=datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc).replace(tzinfo=None),
                apertura=Decimal(k[1]), maximo=Decimal(k[2]), minimo=Decimal(k[3]),
                cierre=Decimal(k[4]), volumen=Decimal(k[5])))
        except (IndexError, TypeError, ValueError, ArithmeticError) as e:
            raise ErrorBinance(f"Vela mal formada en {simbolo} {intervalo}: {str(k)[:200]}") from e
    return velas


def url_klines(base: str, simbolo: str, intervalo: str, desde_ms: int | None, limite: int) -> str:
    params = {"symbol": simbolo, "interval": intervalo, "limit": limite}
    if desde_ms is not None:
        params["startTime"] = desde_ms
    return f"{base.rstrip('/')}/api/v3/klines?{urllib.parse.urlencode(params)}"


def pedir_json(url: str, timeout: float = 20.0, intentos: int = 3, espera: float = 2.0):
    """GET con reintentos para errores temporales. El 451 (bloqueo por país) no se reintenta."""
    ultimo: Exception | None = None
    for n in range(1, intentos + 1):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                       "User-Agent": "securechain-binance-velas"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 451:
                raise ErrorBinance("Binance bloquea esta región (HTTP 451). "
                                   "El trabajo debe correr en Europa (Spain Central o eu-south-2).") from e
            if e.code not in (418, 429) and e.code < 500:
                raise ErrorBinance(f"Binance respondió HTTP {e.code} para {url}") from e
            ultimo = e
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            ultimo = e
        if n < intentos:
            time.sleep(espera * n)
    raise ErrorBinance(f"Binance no respondió tras {intentos} intentos: {ultimo}")


def desde_para(ultima: datetime | None) -> int | None:
    """Desde dónde pedir: la última vela guardada (para reescribirla si seguía abierta) o todo."""
    if ultima is None:
        return None
    return int(ultima.replace(tzinfo=timezone.utc).timestamp() * 1000)
