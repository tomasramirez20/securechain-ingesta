"""Endpoint de salud /healthz sin dependencias externas.

Responde 200 si el servicio recibió datos hace menos de `max_silencio` segundos
y 503 si no. Las sondas de Azure Container Apps (y Docker HEALTHCHECK) lo
consultan y reinician el contenedor cuando se queda colgado.
"""

from __future__ import annotations

import asyncio
import json
import time


class EstadoSalud:
    def __init__(self, max_silencio: float):
        self.max_silencio = max_silencio
        self.inicio = time.monotonic()
        self.ultimo_mensaje: float | None = None
        self.conectado = False
        self.reconexiones = 0
        self.ultimo_error: str | None = None

    def marcar_mensaje(self) -> None:
        self.ultimo_mensaje = time.monotonic()

    def segundos_sin_datos(self) -> float:
        referencia = self.ultimo_mensaje if self.ultimo_mensaje is not None else self.inicio
        return time.monotonic() - referencia

    def sano(self) -> bool:
        return self.segundos_sin_datos() <= self.max_silencio

    def resumen(self) -> dict:
        return {
            "sano": self.sano(),
            "conectado": self.conectado,
            "segundos_sin_datos": round(self.segundos_sin_datos(), 1),
            "reconexiones": self.reconexiones,
            "ultimo_error": self.ultimo_error,
        }


async def servir_salud(estado: EstadoSalud, host: str = "0.0.0.0", puerto: int = 8080):
    async def atender(lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        try:
            primera = await asyncio.wait_for(lector.readline(), timeout=5)
            while True:  # descartar cabeceras
                linea = await asyncio.wait_for(lector.readline(), timeout=5)
                if linea in (b"\r\n", b"\n", b""):
                    break
            partes = primera.split()
            ruta = partes[1].decode("ascii", "replace") if len(partes) > 1 else "/"
            if ruta.split("?")[0] == "/healthz":
                ok = estado.sano()
                estado_http = "200 OK" if ok else "503 Service Unavailable"
                cuerpo = json.dumps(estado.resumen()).encode()
            else:
                estado_http = "404 Not Found"
                cuerpo = b'{"error":"ruta no encontrada"}'
            escritor.write(
                f"HTTP/1.1 {estado_http}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(cuerpo)}\r\nConnection: close\r\n\r\n".encode() + cuerpo
            )
            await escritor.drain()
        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            escritor.close()

    return await asyncio.start_server(atender, host, puerto)
