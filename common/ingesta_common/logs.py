"""Logs estructurados en JSON, una línea por evento, por stdout."""

from __future__ import annotations

import json
import logging
import sys
import time


class FormatoJson(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        datos = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "nivel": record.levelname,
            "servicio": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "datos", None)
        if isinstance(extra, dict):
            datos.update(extra)
        if record.exc_info:
            datos["error"] = self.formatException(record.exc_info)
        return json.dumps(datos, ensure_ascii=False, default=str)


def configurar_logs(servicio: str, nivel: str = "INFO") -> logging.Logger:
    manejador = logging.StreamHandler(sys.stdout)
    manejador.setFormatter(FormatoJson())
    raiz = logging.getLogger()
    raiz.handlers[:] = [manejador]
    raiz.setLevel(nivel.upper())
    logging.getLogger("websockets").setLevel(logging.WARNING)
    return logging.getLogger(servicio)
