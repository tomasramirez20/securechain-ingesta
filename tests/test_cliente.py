"""Pruebas del cliente contra un servidor WebSocket local que imita a Binance."""

import asyncio
import json
import logging
from http import HTTPStatus

from websockets.asyncio.server import serve

from binance_ws.cliente import BinanceTickerCliente
from ingesta_common.backoff import Backoff
from ingesta_common.salud import EstadoSalud

LOG = logging.getLogger("prueba")


def msg(simbolo, precio, evento_ms):
    return json.dumps({"stream": f"{simbolo.lower()}@ticker", "data": {
        "e": "24hrTicker", "E": evento_ms, "s": simbolo, "c": str(precio), "P": "0.5", "v": "10"}})


class Almacen:
    """Sustituye a MySQL: guarda lo que el cliente intenta escribir."""
    def __init__(self, fallos=0):
        self.lotes = []
        self.fallos = fallos

    async def guardar(self, tickers):
        if self.fallos > 0:
            self.fallos -= 1
            raise ConnectionError("MySQL no disponible (simulado)")
        self.lotes.append(list(tickers))

    def ultimo_precio(self, simbolo):
        precios = [t.precio for lote in self.lotes for t in lote if t.simbolo == simbolo]
        return precios[-1] if precios else None


def nuevo_cliente(url, almacen, **kw):
    opciones = dict(flush_s=0.05, timeout_silencio_s=2.0, espera_bloqueo_s=5.0,
                    backoff=Backoff(base=0.05, tope=0.2))
    opciones.update(kw)
    return BinanceTickerCliente(url, almacen.guardar, EstadoSalud(max_silencio=5), LOG, **opciones)


def url_de(srv):
    return f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"


async def correr_hasta(cliente, condicion, limite=5.0):
    tarea = asyncio.create_task(cliente.ejecutar())
    try:
        fin = asyncio.get_running_loop().time() + limite
        while not condicion():
            if asyncio.get_running_loop().time() > fin:
                raise AssertionError("La condición no se cumplió a tiempo")
            await asyncio.sleep(0.02)
    finally:
        cliente.parar()
        await asyncio.wait_for(tarea, timeout=5)


async def test_guarda_el_ultimo_precio_de_cada_simbolo():
    async def manejador(ws):
        await ws.send(msg("BTCUSDT", 100, 1000))
        await ws.send(msg("BTCUSDT", 101, 2000))
        await ws.send(msg("ETHUSDT", 50, 1500))
        await ws.send(json.dumps({"result": None, "id": 1}))
        await ws.wait_closed()

    async with serve(manejador, "127.0.0.1", 0) as srv:
        almacen = Almacen()
        cliente = nuevo_cliente(url_de(srv), almacen)
        await correr_hasta(cliente, lambda: almacen.ultimo_precio("ETHUSDT") is not None
                           and almacen.ultimo_precio("BTCUSDT") == 101)
    assert all(len({t.simbolo for t in lote}) == len(lote) for lote in almacen.lotes)


async def test_reconecta_tras_un_corte():
    conexiones = 0

    async def manejador(ws):
        nonlocal conexiones
        conexiones += 1
        await ws.send(msg("BTCUSDT", 100 + conexiones, conexiones * 1000))
        if conexiones == 1:
            await ws.close(code=1011, reason="caida simulada")
            return
        await ws.wait_closed()

    async with serve(manejador, "127.0.0.1", 0) as srv:
        almacen = Almacen()
        cliente = nuevo_cliente(url_de(srv), almacen)
        await correr_hasta(cliente, lambda: almacen.ultimo_precio("BTCUSDT") == 102)
    assert cliente.conexiones >= 2
    assert cliente.salud.reconexiones >= 1


async def test_reconexion_planificada_antes_del_corte_de_24h():
    async def manejador(ws):
        i = 0
        try:
            while True:
                i += 1
                await ws.send(msg("BTCUSDT", 100, i))
                await asyncio.sleep(0.02)
        except Exception:
            pass

    async with serve(manejador, "127.0.0.1", 0) as srv:
        cliente = nuevo_cliente(url_de(srv), Almacen(), max_conexion_s=0.3)
        await correr_hasta(cliente, lambda: cliente.conexiones >= 3)
    assert cliente.salud.reconexiones == 0


async def test_reconecta_si_la_conexion_queda_en_silencio():
    async def manejador(ws):
        await ws.wait_closed()

    async with serve(manejador, "127.0.0.1", 0) as srv:
        cliente = nuevo_cliente(url_de(srv), Almacen(), timeout_silencio_s=0.2)
        await correr_hasta(cliente, lambda: cliente.conexiones >= 2)
    assert "ConexionSilenciosa" in (cliente.salud.ultimo_error or "")


async def test_bloqueo_451_espera_largo_y_lo_explica(caplog):
    intentos = 0

    def responder_451(conexion, peticion):
        nonlocal intentos
        intentos += 1
        return conexion.respond(HTTPStatus.UNAVAILABLE_FOR_LEGAL_REASONS,
                                "Service unavailable from a restricted location")

    async with serve(lambda ws: None, "127.0.0.1", 0, process_request=responder_451) as srv:
        cliente = nuevo_cliente(url_de(srv), Almacen(), espera_bloqueo_s=10.0)
        with caplog.at_level(logging.ERROR):
            tarea = asyncio.create_task(cliente.ejecutar())
            await asyncio.sleep(0.6)
            cliente.parar()
            await asyncio.wait_for(tarea, timeout=5)
    assert intentos == 1
    assert "451" in cliente.salud.ultimo_error
    assert any("Spain Central" in r.getMessage() for r in caplog.records)


async def test_si_mysql_falla_el_lote_no_se_pierde():
    async def manejador(ws):
        await ws.send(msg("BTCUSDT", 100, 1000))
        await ws.wait_closed()

    async with serve(manejador, "127.0.0.1", 0) as srv:
        almacen = Almacen(fallos=3)
        cliente = nuevo_cliente(url_de(srv), almacen)
        await correr_hasta(cliente, lambda: almacen.ultimo_precio("BTCUSDT") == 100)
    assert almacen.fallos == 0


async def test_dato_viejo_no_pisa_uno_nuevo_en_el_lote():
    async def manejador(ws):
        await ws.send(msg("BTCUSDT", 200, 5000))
        await ws.send(msg("BTCUSDT", 150, 4000))
        await ws.wait_closed()

    async with serve(manejador, "127.0.0.1", 0) as srv:
        almacen = Almacen()
        cliente = nuevo_cliente(url_de(srv), almacen, flush_s=0.3)
        await correr_hasta(cliente, lambda: almacen.ultimo_precio("BTCUSDT") is not None)
    assert almacen.ultimo_precio("BTCUSDT") == 200


async def test_al_parar_guarda_lo_pendiente():
    async def manejador(ws):
        await ws.send(msg("BTCUSDT", 300, 1000))
        await ws.wait_closed()

    async with serve(manejador, "127.0.0.1", 0) as srv:
        almacen = Almacen()
        cliente = nuevo_cliente(url_de(srv), almacen, flush_s=60)
        tarea = asyncio.create_task(cliente.ejecutar())
        while cliente.salud.ultimo_mensaje is None:
            await asyncio.sleep(0.02)
        cliente.parar()
        await asyncio.wait_for(tarea, timeout=5)
    assert almacen.ultimo_precio("BTCUSDT") == 300
