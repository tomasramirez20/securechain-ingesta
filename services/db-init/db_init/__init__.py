"""db-init: prepara la base de datos de SecureChain. Se puede correr las veces que se quiera.

1. Crea la base de datos si no existe.
2. Crea o actualiza las tablas de db/schema.sql.
3. Crea tres usuarios con el mínimo de permisos, tabla por tabla:
     - ingesta:       SELECT, INSERT, UPDATE en las tablas de mercado (binance-ws, binance-velas)
     - api_lectura:   SELECT en las tablas de mercado                 (api-flask, lectura)
     - api_registro:  solo INSERT en solicitudes_sello                (api-flask, formulario del Sello)
   Nadie, salvo el administrador, puede leer las solicitudes: ahí hay datos de contacto.
   Cada usuario recibe la clave que llegue por variable de entorno (si cambia, la actualiza).
   Los permisos se recalculan en cada ejecución (REVOKE y GRANT), así que nunca quedan de más.

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

TABLAS_MERCADO = ("precios", "historicos", "incidentes", "eventos", "velas")

USUARIOS = {
    # usuario: (variable con la clave, {tabla: permisos})
    "ingesta": ("INGESTA_PASSWORD", {t: "SELECT, INSERT, UPDATE" for t in TABLAS_MERCADO}),
    "api_lectura": ("API_PASSWORD", {t: "SELECT" for t in TABLAS_MERCADO}),
    "api_registro": ("REGISTRO_PASSWORD", {"solicitudes_sello": "INSERT"}),
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
        for usuario, (variable, permisos_por_tabla) in USUARIOS.items():
            clave = claves.get(usuario)
            if not clave:
                log.warning("Sin clave para el usuario; no se crea", extra={"datos": {"usuario": usuario,
                                                                                      "variable": variable}})
                continue
            cur.execute(f"CREATE USER IF NOT EXISTS '{usuario}'@'%%' IDENTIFIED BY %s", (clave,))
            cur.execute(f"ALTER USER '{usuario}'@'%%' IDENTIFIED BY %s", (clave,))
            # Sin parámetros PyMySQL no interpreta %, por eso aquí va un solo '%'.
            # Se parte de cero para que no sobrevivan permisos de versiones anteriores
            # (por ejemplo, el antiguo SELECT sobre toda la base).
            cur.execute(f"REVOKE ALL PRIVILEGES, GRANT OPTION FROM '{usuario}'@'%'")
            for tabla, permisos in permisos_por_tabla.items():
                cur.execute(f"GRANT {permisos} ON `{base}`.`{tabla}` TO '{usuario}'@'%'")
            log.info("Usuario listo", extra={"datos": {"usuario": usuario, "permisos": permisos_por_tabla}})
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
