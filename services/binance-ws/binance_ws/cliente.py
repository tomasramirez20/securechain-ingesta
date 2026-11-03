"""Cliente resiliente del WebSocket de Binance.

Según los límites documentados por Binance:
- Una conexión dura máximo 24 h → se reconecta por su cuenta antes (23 h por defecto).
- El servidor envía ping cada 20 s y corta si no hay pong en 1 min → websockets
  responde solo; además enviamos pings propios para detectar conexiones muertas.
- 300 intentos de conexión cada 5 min por IP → reconexión con espera exponencial.
- Bloqueo por ubicación (HTTP 451, servidores en EE. UU.) → error claro y espera larga.
- No se envían mensajes al servidor (la suscripción va en la URL): el límite de
  5 mensajes entrantes por segundo no aplica.

Se guarda el último precio de cada símbolo y se escribe un lote cada `flush_s`
segundos. Si MySQL falla, el lote se conserva y se reintenta.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from ingesta_common.backoff import Backoff
from ingesta_common.salud import EstadoSalud

from .parser import Ticker, parsear_mensaje

Guardar = Callable[[list[Ticker]], Awaitable[object]]


class ConexionSilenciosa(Exception):
    """La conexión sigue abierta pero dejó de llegar información."""


class BinanceTickerCliente:
    def __init__(self, url: str, guardar: Guardar, salud: EstadoSalud, logger: logging.Logger,
                 *, max_conexion_s: float = 23 * 3600, flush_s: float = 2.0,
                 timeout_silencio_s: float = 60.0, espera_bloqueo_s: float = 900.0,
                 timeout_apertura_s: float = 10.0, backoff: Backoff | None = None):
        self.url = url
        self._guardar = guardar
        self.salud = salud
        self.log = logger
        self.max_conexion_s = max_conexion_s
        self.flush_s = flush_s
        self.timeout_silencio_s = timeout_silencio_s
        self.espera_bloqueo_s = espera_bloqueo_s
        self.timeout_apertura_s = timeout_apertura_s
        self.backoff = backoff or Backoff(base=1.0, tope=60.0)
        self._pendientes: dict[str, Ticker] = {}
        self._parar = asyncio.Event()
        self._ws = None
        self.conexiones = 0
        self.tickers_guardados = 0

    def parar(self) -> None:
        """Apagado ordenado (SIGTERM de Docker o Container Apps)."""
        self._parar.set()
        if self._ws is not None:
            asyncio.ensure_future(self._ws.close())

    async def ejecutar(self) -> None:
        tarea_flush = asyncio.create_task(self._bucle_flush())
        try:
            while not self._parar.is_set():
                try:
                    await self._una_conexion()
                    continue  # reconexión planificada o parada: sin espera
                except InvalidStatus as e:
                    codigo = e.response.status_code
                    if codigo == 451:
                        espera = self.espera_bloqueo_s
                        self.salud.ultimo_error = "HTTP 451: Binance bloquea la ubicación de este servidor"
                        self.log.error(
                            "Binance rechazó la conexión por ubicación (HTTP 451). Este servicio debe "
                            "correr en una región no restringida (p. ej. Spain Central), nunca en EE. UU.",
                            extra={"datos": {"espera_s": espera}})
                    else:
                        espera = self.backoff.siguiente()
                        self.salud.ultimo_error = f"HTTP {codigo} en el handshake"
                        self.log.warning("Handshake rechazado",
                                         extra={"datos": {"http": codigo, "espera_s": round(espera, 1)}})
                except (ConnectionClosed, ConexionSilenciosa, InvalidHandshake, OSError, asyncio.TimeoutError) as e:
                    if self._parar.is_set():
                        break
                    espera = self.backoff.siguiente()
                    self.salud.ultimo_error = f"{type(e).__name__}: {e}"
                    self.log.warning("Conexión perdida; se reintentará",
                                     extra={"datos": {"motivo": type(e).__name__, "detalle": str(e)[:200],
                                                      "espera_s": round(espera, 1)}})
                self.salud.reconexiones += 1
                await self._dormir(espera)
        finally:
            tarea_flush.cancel()
            try:
                await tarea_flush
            except asyncio.CancelledError:
                pass
            await self._flush()
            self.log.info("Servicio detenido", extra={"datos": {"tickers_guardados": self.tickers_guardados}})

    async def _una_conexion(self) -> None:
        loop = asyncio.get_running_loop()
        async with connect(self.url, open_timeout=self.timeout_apertura_s, ping_interval=20,
                           ping_timeout=20, close_timeout=5, max_size=2 ** 20) as ws:
            self._ws = ws
            self.conexiones += 1
            self.salud.conectado = True
            inicio = loop.time()
            primer_dato = True
            self.log.info("Conectado a Binance", extra={"datos": {"conexion": self.conexiones}})
            try:
                while not self._parar.is_set():
                    restante = self.max_conexion_s - (loop.time() - inicio)
                    if restante <= 0:
                        self.log.info("Reconexión planificada antes del corte de 24 h de Binance")
                        return
                    try:
                        texto = await asyncio.wait_for(ws.recv(), timeout=min(restante, self.timeout_silencio_s))
                    except asyncio.TimeoutError:
                        if loop.time() - inicio >= self.max_conexion_s:
                            continue
                        raise ConexionSilenciosa(f"Sin datos durante {self.timeout_silencio_s:.0f} s") from None
                    ticker = parsear_mensaje(texto)
                    if ticker is None:
                        continue
                    self.salud.marcar_mensaje()
                    if primer_dato:
                        self.backoff.reiniciar()
                        self.salud.ultimo_error = None
                        primer_dato = False
                    self._acumular(ticker)
            finally:
                self.salud.conectado = False
                self._ws = None

    def _acumular(self, ticker: Ticker) -> None:
        anterior = self._pendientes.get(ticker.simbolo)
        if anterior is None or ticker.evento_ts >= anterior.evento_ts:
            self._pendientes[ticker.simbolo] = ticker

    async def _bucle_flush(self) -> None:
        while True:
            await asyncio.sleep(self.flush_s)
            await self._flush()

    async def _flush(self) -> None:
        if not self._pendientes:
            return
        lote = list(self._pendientes.values())
        self._pendientes = {}
        try:
            await self._guardar(lote)
            self.tickers_guardados += len(lote)
        except Exception as e:
            for t in lote:
                self._acumular(t)
            self.salud.ultimo_error = f"Error guardando en MySQL: {type(e).__name__}"
            self.log.error("No se pudo guardar el lote; se reintentará",
                           extra={"datos": {"filas": len(lote), "motivo": f"{type(e).__name__}: {e}"[:200]}})

    async def _dormir(self, segundos: float) -> None:
        try:
            await asyncio.wait_for(self._parar.wait(), timeout=segundos)
        except asyncio.TimeoutError:
            pass
