"""db-init: prepara la base de datos de SecureChain. Se puede correr las veces que se quiera.

1. Crea la base de datos si no existe.
2. Crea o actualiza las tablas de db/schema.sql.
3. Crea dos usuarios con el mínimo de permisos:
     - ingesta:      SELECT, INSERT, UPDATE  (los microservicios de ingesta)
     - api_lectura:  SELECT                  (api-flask)
   y les asigna la clave que llegue por variable de entorno (si cambia, la actualiza).

Se conecta con el usuario administrador del servidor, que solo usa este trabajo.
Corre dentro de la VNet como trabajo de Container Apps, porque MySQL no tiene acceso público.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymysql

from ingesta_common.config import env_bool, env_int, env_str, kwargs_pymysql
from ingesta_common.logs import configurar_logs

ESQUEMA_DEFECTO = Path(__file__).resolve().parent / "schema.sql"
NOMBRE_VALIDO = re.compile(r"^[A-Za-z0-9_]{1,64}$")

USUARIOS = {
    # usuario: (variable con la clave, permisos)
    "ingesta": ("INGESTA_PASSWORD", "SELECT, INSERT, UPDATE"),
    "api_lectura": ("API_PASSWORD", "SELECT"),
}


def sentencias_sql(texto: str) -> list[str]:
    """Separa un archivo .sql en sentencias, ignorando los comentarios de línea."""
    sin_comentarios = "\n".join(l for l in texto.splitlines() if not l.strip().startswith("--"))
    return [s.strip() for s in sin_comentarios.split(";") if s.strip()]


def preparar(conn, base: str, esquema: str, claves: dict[str, str], log) -> None:
    if not NOMBRE_VALIDO.match(base):
        raise ValueError(f"Nombre de base de datos inválido: {base!r}")
    with conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{base}` CHARACTER SET utf8mb4")
        cur.execute(f"USE `{base}`")
        for s in sentencias_sql(esquema):
            cur.execute(s)
        log.info("Tablas listas", extra={"datos": {"base": base}})
        for usuario, (variable, permisos) in USUARIOS.items():
            clave = claves.get(usuario)
            if not clave:
                log.warning("Sin clave para el usuario; no se crea", extra={"datos": {"usuario": usuario,
                                                                                      "variable": variable}})
                continue
            cur.execute(f"CREATE USER IF NOT EXISTS '{usuario}'@'%%' IDENTIFIED BY %s", (clave,))
            cur.execute(f"ALTER USER '{usuario}'@'%%' IDENTIFIED BY %s", (clave,))
            # Sin parámetros PyMySQL no interpreta %, por eso aquí va un solo '%'.
            cur.execute(f"GRANT {permisos} ON `{base}`.* TO '{usuario}'@'%'")
            log.info("Usuario listo", extra={"datos": {"usuario": usuario, "permisos": permisos}})
    conn.commit()


def principal() -> None:
    log = configurar_logs("db-init", env_str("NIVEL_LOG", "INFO"))
    base = env_str("MYSQL_DATABASE", "securechain")
    params = kwargs_pymysql(
        env_str("MYSQL_HOST", "127.0.0.1"), env_int("MYSQL_PORT", 3306),
        env_str("MYSQL_ADMIN_USER", obligatorio=True), env_str("MYSQL_ADMIN_PASSWORD", obligatorio=True),
        None, env_bool("MYSQL_TLS", True), autocommit=False)
    esquema = Path(env_str("ESQUEMA_SQL", str(ESQUEMA_DEFECTO))).read_text(encoding="utf-8")
    claves = {u: env_str(var) for u, (var, _) in USUARIOS.items()}
    conn = pymysql.connect(**params)
    try:
        preparar(conn, base, esquema, claves, log)
    finally:
        conn.close()
    log.info("Base de datos preparada")
