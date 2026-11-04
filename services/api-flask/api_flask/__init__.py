"""api-flask: la capa de lógica de SecureChain.

Lee lo que la ingesta guardó en MySQL y lo entrega al frontend como JSON.

Endpoints:
  GET /api/precios                    últimos precios (filtro opcional ?fuente=binance)
  GET /api/precios/<simbolo>          un símbolo (BTCUSDT)
  GET /api/historicos?activo=bitcoin&moneda=usd&intervalo=1d[&desde=2026-10-01][&limite=365]
  GET /api/incidentes[?desde=2026-01-01][&limite=50]
  GET /api/eventos[?limite=50]
  GET /api/velas?intervalo=1h|1d[&simbolo=BTCUSDT][&limite=24]   velas OHLCV para gráficas
  POST /api/solicitudes               solicitud del Sello de Confianza (formulario del sitio)
  GET /healthz                        vida del proceso (no toca MySQL)
  GET /readyz                         listo para recibir tráfico (MySQL responde)

Precios y montos se devuelven como texto ("67000.010000000000") para no perder
precisión al pasar por JSON; las fechas, en ISO 8601 UTC con "Z".
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from ingesta_common.config import config_mysql_desde_env, env_lista, env_str
from ingesta_common.logs import configurar_logs

from .registro import LimitadorTasa, Registro, SolicitudInvalida, validar
from .repositorio import Repositorio

INTERVALOS = {"5m", "1h", "1d"}
INTERVALOS_VELAS = {"1h": timedelta(hours=1), "1d": timedelta(days=1)}
LIMITE_VELAS_DEFECTO = {"1h": 24, "1d": 365}
LIMITE_MAXIMO = 1000
SIMBOLO = re.compile(r"^[A-Z0-9]{2,20}$")
CAMPO_TRAMPA = "fax"  # campo oculto del formulario: una persona no lo ve; un bot suele llenarlo


class ErrorPeticion(ValueError):
    """Parámetro inválido en la petición: se responde 400."""


def _serializar(valor):
    if isinstance(valor, Decimal):
        return format(valor, "f")
    if isinstance(valor, datetime):
        if valor.tzinfo is None:
            valor = valor.replace(tzinfo=timezone.utc)
        return valor.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if isinstance(valor, date):
        return valor.isoformat()
    return valor


def _filas(filas: list[dict]) -> list[dict]:
    return [{k: _serializar(v) for k, v in f.items()} for f in filas]


def _limite(defecto: int) -> int:
    texto = request.args.get("limite")
    if texto is None:
        return defecto
    try:
        n = int(texto)
    except ValueError:
        raise ErrorPeticion("limite debe ser un entero") from None
    if not 1 <= n <= LIMITE_MAXIMO:
        raise ErrorPeticion(f"limite debe estar entre 1 y {LIMITE_MAXIMO}")
    return n


def _fecha(nombre: str) -> date | None:
    texto = request.args.get(nombre)
    if not texto:
        return None
    try:
        return date.fromisoformat(texto)
    except ValueError:
        raise ErrorPeticion(f"{nombre} debe tener formato AAAA-MM-DD") from None


def _ms(ts: datetime) -> int:
    return int(ts.replace(tzinfo=timezone.utc).timestamp() * 1000)


def _registro_desde_env() -> Registro | None:
    """El formulario solo se activa si hay credenciales del usuario api_registro."""
    usuario = env_str("MYSQL_REGISTRO_USER")
    clave = env_str("MYSQL_REGISTRO_PASSWORD")
    if not (usuario and clave):
        return None
    cfg = config_mysql_desde_env()
    return Registro(cfg["host"], cfg["puerto"], usuario, clave, cfg["base"], cfg["tls"])


def crear_app(repo: Repositorio | None = None, registro: Registro | None = None,
              limitador: LimitadorTasa | None = None) -> Flask:
    log = configurar_logs("api-flask", env_str("NIVEL_LOG", "INFO"))
    app = Flask(__name__)
    app.json.sort_keys = False
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024  # un formulario nunca pesa más
    repo = repo or Repositorio(**config_mysql_desde_env())
    registro = registro if registro is not None else _registro_desde_env()
    limitador = limitador or LimitadorTasa()
    # CORS desde la app solo para pruebas locales. En Azure lo maneja el ingress
    # de Container Apps (dejar CORS_ORIGENES vacío para no duplicar cabeceras).
    origenes = set(env_lista("CORS_ORIGENES", ""))

    @app.after_request
    def cabeceras(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        origen = request.headers.get("Origin")
        if origen and origen in origenes:
            resp.headers["Access-Control-Allow-Origin"] = origen
            resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
            resp.headers["Vary"] = "Origin"
        return resp

    @app.errorhandler(ErrorPeticion)
    def peticion_invalida(e):
        return jsonify(error=str(e)), 400

    @app.errorhandler(404)
    def no_encontrado(_e):
        return jsonify(error="ruta no encontrada"), 404

    @app.errorhandler(Exception)
    def error_interno(e):
        if isinstance(e, HTTPException):  # 405, 404... se responden tal cual, en JSON
            return jsonify(error=e.description), e.code
        log.error("Error atendiendo la petición", exc_info=e,
                  extra={"datos": {"ruta": request.path}})
        return jsonify(error="error interno"), 500

    @app.get("/healthz")
    def healthz():
        return jsonify(estado="vivo")

    @app.get("/readyz")
    def readyz():
        try:
            repo.ping()
        except Exception as e:
            log.warning("MySQL no responde", extra={"datos": {"motivo": f"{type(e).__name__}: {e}"[:200]}})
            return jsonify(estado="sin base de datos"), 503
        return jsonify(estado="listo")

    @app.get("/api/precios")
    def precios():
        fuente = request.args.get("fuente") or None
        filas = _filas(repo.precios(fuente=fuente))
        resp = jsonify(datos=filas, total=len(filas))
        resp.headers["Cache-Control"] = "public, max-age=2"
        return resp

    @app.get("/api/precios/<simbolo>")
    def precio(simbolo: str):
        filas = _filas(repo.precios(simbolo=simbolo.upper()))
        if not filas:
            return jsonify(error=f"sin datos para {simbolo.upper()}"), 404
        return jsonify(datos=filas)

    @app.get("/api/historicos")
    def historicos():
        activo = request.args.get("activo")
        if not activo:
            raise ErrorPeticion("falta el parámetro activo (p. ej. bitcoin)")
        intervalo = request.args.get("intervalo", "1d")
        if intervalo not in INTERVALOS:
            raise ErrorPeticion(f"intervalo debe ser uno de {sorted(INTERVALOS)}")
        moneda = request.args.get("moneda", "usd").lower()
        desde = _fecha("desde")
        filas = _filas(repo.historicos(activo.lower(), moneda, intervalo,
                                       datetime(desde.year, desde.month, desde.day) if desde else None,
                                       _limite(365)))
        return jsonify(datos=filas, total=len(filas))

    @app.get("/api/incidentes")
    def incidentes():
        filas = _filas(repo.incidentes(_fecha("desde"), _limite(50)))
        return jsonify(datos=filas, total=len(filas))

    @app.get("/api/velas")
    def velas():
        intervalo = request.args.get("intervalo", "1h")
        if intervalo not in INTERVALOS_VELAS:
            raise ErrorPeticion(f"intervalo debe ser uno de {sorted(INTERVALOS_VELAS)}")
        simbolo = (request.args.get("simbolo") or "").upper() or None
        if simbolo and not SIMBOLO.match(simbolo):
            raise ErrorPeticion("simbolo inválido (p. ej. BTCUSDT)")
        limite = _limite(LIMITE_VELAS_DEFECTO[intervalo])
        desde = (datetime.now(timezone.utc).replace(tzinfo=None)
                 - INTERVALOS_VELAS[intervalo] * limite)
        series: dict[str, list] = {}
        for f in repo.velas(intervalo, desde, simbolo):
            # Formato compacto para gráficas: [ms, apertura, máximo, mínimo, cierre, volumen].
            series.setdefault(f["simbolo"], []).append(
                [_ms(f["apertura_ts"]), float(f["apertura"]), float(f["maximo"]),
                 float(f["minimo"]), float(f["cierre"]), float(f["volumen"])])
        resp = jsonify(intervalo=intervalo, series=series)
        resp.headers["Cache-Control"] = "public, max-age=60"
        return resp

    @app.post("/api/solicitudes")
    def solicitudes():
        if registro is None:
            return jsonify(error="el formulario no está disponible en este momento"), 503
        datos = request.get_json(silent=True)
        if isinstance(datos, dict) and datos.get(CAMPO_TRAMPA):
            # Un bot llenó el campo oculto: se le responde como si nada, pero no se guarda.
            log.info("Solicitud descartada por el campo trampa")
            return jsonify(referencia="SC-RECIBIDA"), 201
        cliente = (request.headers.get("X-Forwarded-For") or request.remote_addr or "?").split(",")[0].strip()
        if not limitador.permitir(cliente):
            return jsonify(error="demasiadas solicitudes; intenta de nuevo en unos minutos"), 429
        try:
            limpio = validar(datos)
        except SolicitudInvalida as e:
            return jsonify(error="revisa los campos marcados", campos=e.errores), 400
        referencia = registro.guardar(limpio)
        # Nunca se registran datos de contacto en los logs: solo la referencia y el tipo.
        log.info("Solicitud del Sello recibida", extra={"datos": {"referencia": referencia,
                                                                  "tipo": limpio["tipo"],
                                                                  "interes": limpio["interes"]}})
        return jsonify(referencia=referencia), 201

    @app.get("/api/eventos")
    def eventos():
        filas = _filas(repo.eventos(_limite(50)))
        return jsonify(datos=filas, total=len(filas))

    return app
