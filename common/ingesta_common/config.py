"""Configuración desde variables de entorno.

Regla de la casa: nada de credenciales en el código ni en la imagen.
Un secreto se lee de NOMBRE o, si existe, del archivo indicado en NOMBRE_FILE
(sirve igual con secretos de Container Apps, Key Vault o Docker secrets).
"""

from __future__ import annotations

import os

# Bundle de autoridades de certificación del sistema (viene en las imágenes python:*-slim).
CA_SISTEMA = "/etc/ssl/certs/ca-certificates.crt"


class ErrorConfiguracion(RuntimeError):
    """Falta una variable obligatoria o tiene un valor inválido."""


def env_str(nombre: str, defecto: str | None = None, obligatorio: bool = False) -> str | None:
    ruta_archivo = os.environ.get(f"{nombre}_FILE")
    if ruta_archivo:
        try:
            with open(ruta_archivo, encoding="utf-8") as f:
                return f.read().strip()
        except OSError as e:
            raise ErrorConfiguracion(f"No se pudo leer {nombre}_FILE={ruta_archivo}: {e}") from e
    valor = os.environ.get(nombre)
    if valor is None or valor == "":
        if obligatorio:
            raise ErrorConfiguracion(f"Falta la variable de entorno obligatoria {nombre}")
        return defecto
    return valor


def env_int(nombre: str, defecto: int) -> int:
    valor = env_str(nombre)
    if valor is None:
        return defecto
    try:
        return int(valor)
    except ValueError as e:
        raise ErrorConfiguracion(f"{nombre} debe ser un entero, llegó {valor!r}") from e


def env_float(nombre: str, defecto: float) -> float:
    valor = env_str(nombre)
    if valor is None:
        return defecto
    try:
        return float(valor)
    except ValueError as e:
        raise ErrorConfiguracion(f"{nombre} debe ser un número, llegó {valor!r}") from e


def env_bool(nombre: str, defecto: bool) -> bool:
    valor = env_str(nombre)
    if valor is None:
        return defecto
    return valor.strip().lower() in ("1", "true", "si", "sí", "yes", "on")


def env_lista(nombre: str, defecto: str) -> list[str]:
    valor = env_str(nombre, defecto) or ""
    return [x.strip() for x in valor.split(",") if x.strip()]


def config_mysql_desde_env(prefijo: str = "MYSQL") -> dict:
    """Parámetros de conexión. La clave acepta <PREFIJO>_PASSWORD_FILE.

    MYSQL_TLS=true (por defecto) cifra la conexión y verifica el certificado del
    servidor, como exige Azure Database for MySQL. MYSQL_TLS=false solo para
    pruebas locales.
    """
    return {
        "host": env_str(f"{prefijo}_HOST", "127.0.0.1"),
        "puerto": env_int(f"{prefijo}_PORT", 3306),
        "usuario": env_str(f"{prefijo}_USER", obligatorio=True),
        "clave": env_str(f"{prefijo}_PASSWORD", obligatorio=True),
        "base": env_str("MYSQL_DATABASE", "securechain"),
        "tls": env_bool("MYSQL_TLS", True),
    }


def kwargs_pymysql(host: str, puerto: int, usuario: str, clave: str, base: str | None,
                   tls: bool = True, **extra) -> dict:
    """Convierte la configuración en argumentos para pymysql.connect()."""
    kwargs = dict(host=host, port=puerto, user=usuario, password=clave, charset="utf8mb4",
                  connect_timeout=10, read_timeout=30, write_timeout=30, **extra)
    if base:
        kwargs["database"] = base
    if tls:
        kwargs["ssl_ca"] = env_str("MYSQL_SSL_CA", CA_SISTEMA)
        kwargs["ssl_verify_cert"] = True
        kwargs["ssl_verify_identity"] = True
    return kwargs
