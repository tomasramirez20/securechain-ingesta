"""Lista las últimas solicitudes del Sello de Confianza: `python -m db_init.solicitudes`.

La tabla solo la puede leer el administrador de MySQL, y MySQL no tiene acceso
público; por eso la consulta corre como una ejecución puntual del trabajo db-init,
dentro de la VNet. El resultado sale en los logs de esa ejecución.

Variables: las mismas de db-init, más SOLICITUDES_LIMITE (20).
"""

from __future__ import annotations

import pymysql
import pymysql.cursors

from ingesta_common.config import env_bool, env_int, env_str, kwargs_pymysql
from ingesta_common.logs import configurar_logs


def principal() -> None:
    log = configurar_logs("solicitudes", env_str("NIVEL_LOG", "INFO"))
    params = kwargs_pymysql(
        env_str("MYSQL_HOST", "127.0.0.1"), env_int("MYSQL_PORT", 3306),
        env_str("MYSQL_ADMIN_USER", obligatorio=True), env_str("MYSQL_ADMIN_PASSWORD", obligatorio=True),
        env_str("MYSQL_DATABASE", "securechain"), env_bool("MYSQL_TLS", True),
        cursorclass=pymysql.cursors.DictCursor)
    conn = pymysql.connect(**params)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS total FROM solicitudes_sello")
            total = cur.fetchone()["total"]
            cur.execute("SELECT referencia, creado_en, empresa, sitio_web, tipo, pais, contacto, cargo, "
                        "correo, interes, mensaje FROM solicitudes_sello ORDER BY id DESC LIMIT %s",
                        (env_int("SOLICITUDES_LIMITE", 20),))
            filas = cur.fetchall()
    finally:
        conn.close()
    log.info("Solicitudes del Sello", extra={"datos": {"total": total, "mostradas": len(filas)}})
    for f in filas:
        log.info("Solicitud", extra={"datos": f})


if __name__ == "__main__":
    principal()
