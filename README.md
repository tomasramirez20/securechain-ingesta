# SecureChain · Plataforma nube nativa

Arquitectura de 3 capas sin máquinas virtuales:

| Capa | Servicio | Dónde corre en Azure |
| --- | --- | --- |
| Mostrar | `web/` (HTML, CSS y JS estáticos) | Storage Account con sitio estático |
| Decidir | `api-flask` | Container Apps, HTTPS, escala de 0 a 2 réplicas |
| Proteger | MySQL | Azure Database for MySQL Flexible Server, solo acceso privado |
| Ingesta | `binance-ws` | Container Apps, 1 réplica siempre encendida |
| Preparación | `db-init` | Trabajo de Container Apps que se ejecuta a pedido |

```
Usuario ──HTTPS──> sitio estático ──fetch HTTPS + CORS──> api-flask ─┐
                                                                     ├─ VNet privada ─> MySQL
Binance ──WSS──> binance-ws ─────────────────────────────────────────┘
```

Seguridad:
- MySQL no tiene IP pública; solo se alcanza desde la VNet y con TLS verificado.
- Cada servicio tiene su propio usuario de MySQL con el mínimo de permisos: `ingesta` puede SELECT, INSERT y UPDATE; `api_lectura` solo SELECT.
- Las claves nunca están en el código ni en las imágenes: son secretos de Container Apps.
- La API solo responde por HTTPS y su CORS solo admite el sitio estático.

## Estructura

```
common/ingesta_common/   piezas compartidas: config + TLS, logs JSON, backoff, /healthz, escritura en MySQL
services/binance-ws/     precios en tiempo real desde Binance
services/api-flask/      API JSON de solo lectura
services/db-init/        crea tablas y usuarios (se puede repetir)
db/schema.sql            precios, historicos, incidentes, eventos
web/                     frontend estático
deploy/                  script de despliegue en Azure + plantillas de Container Apps
tests/                   pruebas (Binance simulado, API, MySQL real)
.github/workflows/ci.yml pruebas + publicación de las 3 imágenes en GitHub Container Registry
```

## 1. Subir a GitHub y publicar las imágenes

1. Crear el repositorio `securechain-ingesta` en GitHub y subir estos archivos (incluida la carpeta `.github`).
2. La pestaña **Actions** corre las pruebas y publica `securechain-binance-ws`, `securechain-api-flask` y `securechain-db-init` en `ghcr.io/<usuario>/`.
3. En GitHub → tu perfil → **Packages**, abrir cada paquete → *Package settings* → *Change visibility* → **Public**. Así Container Apps descarga las imágenes sin credenciales. Las imágenes no contienen claves.

## 2. Desplegar en Azure (Cloud Shell, Bash)

```bash
git clone https://github.com/<usuario>/securechain-ingesta.git
cd securechain-ingesta
./deploy/desplegar.sh verificar                       # suscripción y regiones permitidas
./deploy/desplegar.sh preparar                        # registra proveedores (una vez)
UBICACION=<region> ./deploy/desplegar.sh red
./deploy/desplegar.sh mysql                           # 5 a 10 minutos
./deploy/desplegar.sh entorno
DUENO=<usuario-github> ./deploy/desplegar.sh dbinit
./deploy/desplegar.sh binance
./deploy/desplegar.sh web
./deploy/desplegar.sh api
./deploy/desplegar.sh subir-web
./deploy/desplegar.sh estado                          # resumen y prueba de la API
```

Cada paso se puede repetir. Las claves generadas quedan en `deploy/.estado` (fuera de git). Guarda una copia en un lugar seguro: si Cloud Shell se reinicia sin almacenamiento, ese archivo se pierde. Las claves siguen en Azure como secretos.

**Región:** Binance bloquea servidores en EE. UU. con el error HTTP 451. Usa una región europea si la suscripción lo permite. Si solo permite regiones de EE. UU., la fuente de tiempo real debe cambiarse (por ejemplo, al WebSocket público de Coinbase).

**Consumo:** MySQL B1MS está cubierto 12 meses por Azure for Students. `api-flask`, `db-init` y Storage entran en la cuota gratuita o cuestan centavos. `binance-ws` está siempre encendido y es lo que más consume. Crea una alerta de presupuesto en Cost Management.

## 3. Probar en tu computador

```bash
cp .env.example .env            # cambiar las claves
docker compose up --build       # MySQL + db-init + binance-ws + api-flask
curl http://127.0.0.1:8000/api/precios
```

Para el frontend, poner `window.SECURECHAIN_API = "http://127.0.0.1:8000";` en `web/config.js` y servir `web/` (por ejemplo con `python -m http.server 5500 -d web`).

Pruebas:

```bash
pip install -r requirements-dev.txt ./common
pytest -v        # las de MySQL real corren si se definen MYSQL_TEST_HOST, MYSQL_TEST_ADMIN_USER y MYSQL_TEST_ADMIN_PASSWORD
```

## API

| Ruta | Qué devuelve |
| --- | --- |
| `GET /api/precios` | Último precio de cada símbolo (`?fuente=binance` opcional) |
| `GET /api/precios/<simbolo>` | Un símbolo, por ejemplo `BTCUSDT` |
| `GET /api/historicos?activo=bitcoin&moneda=usd&intervalo=1d` | Serie histórica (`desde`, `limite` opcionales) |
| `GET /api/incidentes` | Hacks y exploits recientes (`desde`, `limite`) |
| `GET /api/eventos` | Mercados de predicción |
| `GET /healthz` | El proceso está vivo |
| `GET /readyz` | MySQL responde (Container Apps no envía tráfico si falla) |

Precios y montos van como texto para no perder decimales; las fechas, en UTC con formato ISO 8601.

## Qué hace `binance-ws`

- Escucha `{símbolo}@ticker` en un solo stream combinado y escribe el último precio de cada símbolo cada 2 s.
- Upsert idempotente: un dato repetido no duplica filas y uno más viejo no reemplaza a uno más nuevo.
- Se reconecta por su cuenta cada 23 h (Binance corta toda conexión a las 24 h) y cuando pasan 60 s sin datos.
- Ante cortes, reintenta con espera exponencial; ante HTTP 451 lo dice en el log y espera 15 minutos.
- Si MySQL falla, conserva el lote y lo reintenta.
- `/healthz` responde 503 si pasan 90 s sin datos, y Container Apps reinicia el contenedor.
