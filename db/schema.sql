-- SecureChain · Esquema de ingesta de datos de mercado
-- Compatible con MySQL 5.7+, MySQL 8.x (incluido Azure Database for MySQL Flexible Server)
-- y MariaDB 10.6+. Todas las fechas se guardan en UTC.
-- Cada tabla tiene una clave única natural: reescribir el mismo dato
-- actualiza la fila en vez de duplicarla (escrituras idempotentes).

-- Último precio conocido por fuente y símbolo (alimentada por binance-ws).
CREATE TABLE IF NOT EXISTS precios (
  fuente             VARCHAR(20)    NOT NULL,
  simbolo            VARCHAR(30)    NOT NULL,
  precio             DECIMAL(30,12) NOT NULL,
  variacion_24h_pct  DECIMAL(12,4)  NULL,
  volumen_24h        DECIMAL(38,8)  NULL,
  evento_ts          DATETIME(3)    NOT NULL COMMENT 'Hora del evento en la fuente (UTC)',
  actualizado_en     TIMESTAMP(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (fuente, simbolo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Series históricas agregadas (alimentada por coingecko-job).
CREATE TABLE IF NOT EXISTS historicos (
  fuente             VARCHAR(20)    NOT NULL,
  activo             VARCHAR(80)    NOT NULL,
  moneda_cotizacion  VARCHAR(10)    NOT NULL,
  intervalo          VARCHAR(5)     NOT NULL COMMENT '5m, 1h o 1d',
  ts                 DATETIME       NOT NULL,
  precio             DECIMAL(30,12) NOT NULL,
  capitalizacion     DECIMAL(38,2)  NULL,
  volumen            DECIMAL(38,2)  NULL,
  PRIMARY KEY (fuente, activo, moneda_cotizacion, intervalo, ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Incidentes de seguridad: hacks y exploits.
CREATE TABLE IF NOT EXISTS incidentes (
  fuente             VARCHAR(20)    NOT NULL,
  id_externo         VARCHAR(100)   NOT NULL,
  fecha              DATE           NOT NULL,
  protocolo          VARCHAR(150)   NOT NULL,
  cadena             VARCHAR(80)    NULL,
  tecnica            VARCHAR(200)   NULL,
  monto_usd          DECIMAL(20,2)  NULL,
  url_referencia     VARCHAR(500)   NULL,
  registrado_en      TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (fuente, id_externo),
  KEY idx_incidentes_fecha (fecha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Eventos de mercados de predicción (alimentada por polymarket-ws).
CREATE TABLE IF NOT EXISTS eventos (
  fuente             VARCHAR(20)    NOT NULL,
  id_activo          VARCHAR(120)   NOT NULL,
  id_mercado         VARCHAR(120)   NULL,
  pregunta           VARCHAR(500)   NULL,
  precio             DECIMAL(10,6)  NOT NULL,
  evento_ts          DATETIME(3)    NOT NULL,
  actualizado_en     TIMESTAMP(3)   NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
  PRIMARY KEY (fuente, id_activo)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Velas (OHLCV) de Binance para las gráficas del sitio (alimentada por el trabajo binance-velas).
-- intervalo: 1h (últimos días) o 1d (varios años). La vela abierta se reescribe hasta que cierra.
CREATE TABLE IF NOT EXISTS velas (
  fuente             VARCHAR(20)    NOT NULL,
  simbolo            VARCHAR(30)    NOT NULL,
  intervalo          VARCHAR(5)     NOT NULL,
  apertura_ts        DATETIME       NOT NULL COMMENT 'Inicio de la vela (UTC)',
  apertura           DECIMAL(30,12) NOT NULL,
  maximo             DECIMAL(30,12) NOT NULL,
  minimo             DECIMAL(30,12) NOT NULL,
  cierre             DECIMAL(30,12) NOT NULL,
  volumen            DECIMAL(38,8)  NOT NULL,
  actualizado_en     TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (fuente, simbolo, intervalo, apertura_ts),
  KEY idx_velas_intervalo_ts (intervalo, apertura_ts)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Solicitudes del Sello de Confianza enviadas desde el sitio por exchanges y brokers.
-- Solo el usuario api_registro puede escribir aquí (INSERT) y ningún servicio público puede leerla.
CREATE TABLE IF NOT EXISTS solicitudes_sello (
  id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  creado_en          TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  empresa            VARCHAR(120)   NOT NULL,
  sitio_web          VARCHAR(200)   NOT NULL,
  tipo               VARCHAR(20)    NOT NULL COMMENT 'exchange, broker, custodio u otro',
  pais               VARCHAR(60)    NOT NULL,
  contacto           VARCHAR(120)   NOT NULL,
  cargo              VARCHAR(80)    NULL,
  correo             VARCHAR(160)   NOT NULL,
  interes            VARCHAR(20)    NOT NULL COMMENT 'diagnostico, sello o renovacion',
  mensaje            VARCHAR(1000)  NULL,
  referencia         CHAR(10)       NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_solicitudes_referencia (referencia)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
