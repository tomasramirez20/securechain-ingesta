import asyncio
import json
import random

from ingesta_common.backoff import Backoff
from ingesta_common.config import kwargs_pymysql
from ingesta_common.salud import EstadoSalud, servir_salud


def test_backoff_crece_hasta_el_tope_y_se_reinicia():
    b = Backoff(base=1, tope=8, rng=random.Random(42))
    for techo in [1, 2, 4, 8, 8, 8]:
        espera = b.siguiente()
        assert techo / 2 <= espera <= techo
    b.reiniciar()
    assert b.siguiente() <= 1


def test_backoff_no_desborda_tras_muchos_fallos():
    b = Backoff(base=1, tope=60)
    for _ in range(10_000):
        assert b.siguiente() <= 60


def test_tls_activado_verifica_certificado_y_nombre():
    k = kwargs_pymysql("srv.mysql.database.azure.com", 3306, "u", "c", "db", tls=True)
    assert k["ssl_verify_cert"] is True and k["ssl_verify_identity"] is True
    assert k["ssl_ca"].endswith(".crt")
    assert "ssl_ca" not in kwargs_pymysql("127.0.0.1", 3306, "u", "c", "db", tls=False)


async def _get(puerto, ruta="/healthz"):
    lector, escritor = await asyncio.open_connection("127.0.0.1", puerto)
    escritor.write(f"GET {ruta} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    await escritor.drain()
    respuesta = await lector.read()
    escritor.close()
    cabecera, _, cuerpo = respuesta.partition(b"\r\n\r\n")
    return int(cabecera.split()[1]), cuerpo


async def test_healthz_503_sin_datos_y_200_con_datos():
    estado = EstadoSalud(max_silencio=0.2)
    servidor = await servir_salud(estado, host="127.0.0.1", puerto=0)
    puerto = servidor.sockets[0].getsockname()[1]
    try:
        await asyncio.sleep(0.3)
        assert (await _get(puerto))[0] == 503
        estado.marcar_mensaje()
        codigo, cuerpo = await _get(puerto)
        assert codigo == 200 and json.loads(cuerpo)["sano"] is True
        assert (await _get(puerto, "/otra"))[0] == 404
    finally:
        servidor.close()
        await servidor.wait_closed()
