"""Registro de solicitudes del Sello de Confianza.

Usa un usuario de MySQL distinto al de lectura (api_registro), que solo tiene
permiso INSERT sobre la tabla solicitudes_sello: puede guardar una solicitud,
pero no leer las demás ni tocar ninguna otra tabla. Si alguien abusara del
formulario, lo peor que podría hacer es agregar filas.
"""

from __future__ import annotations

import re
import secrets
import string
import threading
import time
from collections import deque

import pymysql

from ingesta_common.config import kwargs_pymysql

TIPOS = {"exchange", "broker", "custodio", "otro"}
INTERESES = {"diagnostico", "sello", "renovacion"}
CORREO = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
SITIO = re.compile(r"^(https?://)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(/\S*)?$")
ALFABETO_REF = string.ascii_uppercase + string.digits

# campo: (obligatorio, largo máximo)
CAMPOS = {
    "empresa": (True, 120), "sitio_web": (True, 200), "tipo": (True, 20), "pais": (True, 60),
    "contacto": (True, 120), "cargo": (False, 80), "correo": (True, 160),
    "interes": (True, 20), "mensaje": (False, 1000),
}

SQL_INSERTAR = ("INSERT INTO solicitudes_sello "
                "(empresa, sitio_web, tipo, pais, contacto, cargo, correo, interes, mensaje, referencia) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")


class SolicitudInvalida(ValueError):
    def __init__(self, errores: dict[str, str]):
        super().__init__("solicitud inválida")
        self.errores = errores


def _limpiar(texto) -> str:
    if not isinstance(texto, str):
        return ""
    # Sin caracteres de control (salvo saltos de línea en el mensaje) ni espacios sobrantes.
    return "".join(c for c in texto if c in "\n\t" or ord(c) >= 32).strip()


def validar(datos: dict) -> dict[str, str | None]:
    """Valida y normaliza el formulario. Lanza SolicitudInvalida con un error por campo."""
    if not isinstance(datos, dict):
        raise SolicitudInvalida({"_": "se esperaba un objeto JSON"})
    limpio: dict[str, str | None] = {}
    errores: dict[str, str] = {}
    for campo, (obligatorio, largo) in CAMPOS.items():
        valor = _limpiar(datos.get(campo))
        if campo != "mensaje":
            valor = " ".join(valor.split())
        if not valor:
            if obligatorio:
                errores[campo] = "es obligatorio"
            limpio[campo] = None
            continue
        if len(valor) > largo:
            errores[campo] = f"máximo {largo} caracteres"
        limpio[campo] = valor
    if limpio.get("tipo") and limpio["tipo"] not in TIPOS:
        errores["tipo"] = "valor no permitido"
    if limpio.get("interes") and limpio["interes"] not in INTERESES:
        errores["interes"] = "valor no permitido"
    if limpio.get("correo") and not CORREO.match(limpio["correo"]):
        errores["correo"] = "no parece un correo válido"
    if limpio.get("sitio_web") and not SITIO.match(limpio["sitio_web"]):
        errores["sitio_web"] = "no parece una dirección web válida"
    if datos.get("acepta") is not True:
        errores["acepta"] = "debe aceptar el tratamiento de datos"
    if errores:
        raise SolicitudInvalida(errores)
    return limpio


def referencia_nueva() -> str:
    return "SC-" + "".join(secrets.choice(ALFABETO_REF) for _ in range(7))


class LimitadorTasa:
    """Máximo `max_por_ventana` solicitudes por cliente en `ventana_s` segundos (en memoria, por proceso).

    No guarda la dirección IP en la base: solo la usa en memoria para frenar envíos masivos.
    """

    def __init__(self, max_por_ventana: int = 5, ventana_s: float = 600.0, reloj=time.monotonic):
        self.max = max_por_ventana
        self.ventana = ventana_s
        self.reloj = reloj
        self._envios: dict[str, deque] = {}
        self._candado = threading.Lock()

    def permitir(self, cliente: str) -> bool:
        ahora = self.reloj()
        with self._candado:
            if len(self._envios) > 10_000:  # evita crecer sin límite
                self._envios.clear()
            cola = self._envios.setdefault(cliente, deque())
            while cola and ahora - cola[0] > self.ventana:
                cola.popleft()
            if len(cola) >= self.max:
                return False
            cola.append(ahora)
            return True


class Registro:
    def __init__(self, host: str, puerto: int, usuario: str, clave: str, base: str, tls: bool = True):
        self._params = kwargs_pymysql(host, puerto, usuario, clave, base, tls, autocommit=True)

    def guardar(self, solicitud: dict) -> str:
        for _ in range(3):  # la referencia es única; si chocara (muy improbable), se genera otra
            ref = referencia_nueva()
            conn = pymysql.connect(**self._params)
            try:
                with conn.cursor() as cur:
                    cur.execute(SQL_INSERTAR, tuple(solicitud[c] for c in CAMPOS) + (ref,))
                return ref
            except pymysql.err.IntegrityError:
                continue
            finally:
                conn.close()
        raise RuntimeError("no se pudo generar una referencia única")
