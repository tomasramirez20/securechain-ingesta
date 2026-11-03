#!/usr/bin/env bash
# SecureChain · despliegue nube nativa en Azure, paso por paso.
#
# Se ejecuta en Azure Cloud Shell (Bash), dentro del repositorio clonado:
#   git clone https://github.com/<usuario>/securechain-ingesta.git && cd securechain-ingesta
#   UBICACION=<region> DUENO=<usuario-github> ./deploy/desplegar.sh <paso>
#
# Pasos, en orden (cada uno se puede repetir sin romper nada):
#   verificar   Muestra la suscripción y las regiones permitidas. No crea nada.
#   preparar    Registra los proveedores de recursos que usa el proyecto. Sin costo.
#   red         Grupo de recursos + VNet con 3 subredes. Sin costo.
#   mysql       MySQL Flexible Server B1MS, solo acceso privado. Gratis 12 meses (Azure for Students).
#   entorno     Log Analytics + entorno de Container Apps en la VNet.
#   dbinit      Trabajo db-init: crea tablas y usuarios con permisos mínimos.
#   binance     Contenedor binance-ws (siempre encendido: consume crédito).
#   web         Storage Account con sitio estático.
#   api         Contenedor api-flask con HTTPS y CORS solo para el sitio estático.
#   subir-web   Sube el frontend con la dirección de la API.
#   estado      Resumen de lo desplegado y verificación rápida.
#
# Las claves se generan aquí, se guardan en deploy/.estado (permisos 600, fuera de
# git) y en Azure solo como secretos de Container Apps. Nunca se imprimen.
set -euo pipefail
cd "$(dirname "$0")/.."

ESTADO=deploy/.estado
umask 077
touch "$ESTADO"
# shellcheck disable=SC1090
source "$ESTADO"

GRUPO=${GRUPO:-rg-securechain}
VNET=${VNET:-vnet-securechain}
ENTORNO=${ENTORNO:-cae-securechain}
LOGS=${LOGS:-log-securechain}
BASE=${BASE:-securechain}
ADMIN_MYSQL=${ADMIN_MYSQL:-scadmin}
SIMBOLOS=${SIMBOLOS:-btcusdt,ethusdt,solusdt,bnbusdt,xrpusdt,adausdt}
ETIQUETAS="proyecto=securechain"

guardar() {  # guardar NOMBRE valor  → persiste en deploy/.estado
  local nombre=$1 valor=$2
  grep -v "^${nombre}=" "$ESTADO" > "$ESTADO.tmp" || true
  printf "%s='%s'\n" "$nombre" "$valor" >> "$ESTADO.tmp"
  mv "$ESTADO.tmp" "$ESTADO"
  printf -v "$nombre" '%s' "$valor"
}

clave_nueva() {  # 3 categorías garantizadas (mayúscula, minúscula, dígito) + 24 aleatorios
  printf 'Sc9%s' "$(tr -dc 'A-Za-z0-9' < /dev/urandom | head -c 24)"
}

requerir() {
  for v in "$@"; do
    if [ -z "${!v:-}" ]; then echo "Falta $v. Ejemplo: $v=... ./deploy/desplegar.sh $PASO" >&2; exit 1; fi
  done
}

titulo() { printf '\n== %s ==\n' "$*"; }

renderizar() {  # renderizar plantilla.yaml → archivo temporal (permisos 600) con las ${VARIABLES} reemplazadas
  local salida
  salida=$(mktemp)
  python3 -c 'import os, sys; sys.stdout.write(os.path.expandvars(open(sys.argv[1], encoding="utf-8").read()))' "$1" > "$salida"
  if grep -q '\${' "$salida"; then echo "Faltan variables en $1: $(grep -o '\${[A-Z_]*}' "$salida" | sort -u | tr '\n' ' ')" >&2; rm -f "$salida"; exit 1; fi
  echo "$salida"
}

PASO=${1:-}
case "$PASO" in

verificar)
  titulo "Suscripción activa"
  az account show --query "{nombre:name, id:id, estado:state}" -o table
  titulo "Regiones permitidas por política (vacío = sin restricción)"
  az policy assignment list --query "[?contains(displayName, 'llowed') || contains(displayName, 'ermitid')].{politica:displayName, regiones:parameters.listOfAllowedLocations.value}" -o json
  echo
  echo "Binance bloquea servidores en EE. UU. (HTTP 451): elige una región europea si está permitida."
  ;;

preparar)
  titulo "Registrando proveedores de recursos (solo la primera vez, puede tardar unos minutos)"
  for p in Microsoft.App Microsoft.OperationalInsights Microsoft.DBforMySQL Microsoft.Network Microsoft.Storage; do
    az provider register --namespace "$p" --wait
    echo "  $p registrado"
  done
  ;;

red)
  requerir UBICACION
  guardar UBICACION "$UBICACION"
  titulo "Grupo de recursos $GRUPO en $UBICACION"
  az group create -n "$GRUPO" -l "$UBICACION" --tags $ETIQUETAS -o none
  titulo "VNet $VNET (10.10.0.0/16) con subredes privadas"
  az network vnet create -g "$GRUPO" -n "$VNET" -l "$UBICACION" --address-prefixes 10.10.0.0/16 \
    --subnet-name snet-apps --subnet-prefixes 10.10.0.0/24 --tags $ETIQUETAS -o none
  az network vnet subnet update -g "$GRUPO" --vnet-name "$VNET" -n snet-apps \
    --delegations Microsoft.App/environments -o none
  az network vnet subnet create -g "$GRUPO" --vnet-name "$VNET" -n snet-mysql \
    --address-prefixes 10.10.2.0/24 --delegations Microsoft.DBforMySQL/flexibleServers -o none
  # Reservada y vacía: aquí se conectaría más adelante una VPN o ExpressRoute (ver guía).
  az network vnet subnet create -g "$GRUPO" --vnet-name "$VNET" -n GatewaySubnet \
    --address-prefixes 10.10.255.0/27 -o none
  az network vnet subnet list -g "$GRUPO" --vnet-name "$VNET" --query "[].{subred:name, rango:addressPrefix}" -o table
  ;;

mysql)
  requerir UBICACION
  [ -n "${MYSQL_SERVIDOR:-}" ] || guardar MYSQL_SERVIDOR "mysql-securechain-$(tr -dc 'a-z0-9' < /dev/urandom | head -c 6)"
  [ -n "${CLAVE_ADMIN:-}" ] || guardar CLAVE_ADMIN "$(clave_nueva)"
  titulo "MySQL Flexible Server $MYSQL_SERVIDOR (B1MS, 32 GB, sin IP pública) — tarda 5 a 10 minutos"
  if ! az mysql flexible-server show -g "$GRUPO" -n "$MYSQL_SERVIDOR" -o none 2>/dev/null; then
    az mysql flexible-server create -g "$GRUPO" -n "$MYSQL_SERVIDOR" -l "$UBICACION" \
      --admin-user "$ADMIN_MYSQL" --admin-password "$CLAVE_ADMIN" \
      --tier Burstable --sku-name Standard_B1ms --storage-size 32 \
      --high-availability Disabled --backup-retention 7 \
      --vnet "$VNET" --subnet snet-mysql \
      --private-dns-zone "${MYSQL_SERVIDOR}.private.mysql.database.azure.com" \
      --tags $ETIQUETAS --yes -o none
  fi
  guardar MYSQL_FQDN "$(az mysql flexible-server show -g "$GRUPO" -n "$MYSQL_SERVIDOR" --query fullyQualifiedDomainName -o tsv)"
  echo "Servidor listo: $MYSQL_FQDN (solo accesible desde la VNet)"
  ;;

entorno)
  requerir UBICACION
  titulo "Log Analytics $LOGS"
  az monitor log-analytics workspace create -g "$GRUPO" -n "$LOGS" -l "$UBICACION" --retention-time 30 --tags $ETIQUETAS -o none
  ws_id=$(az monitor log-analytics workspace show -g "$GRUPO" -n "$LOGS" --query customerId -o tsv)
  ws_clave=$(az monitor log-analytics workspace get-shared-keys -g "$GRUPO" -n "$LOGS" --query primarySharedKey -o tsv)
  titulo "Entorno de Container Apps $ENTORNO en snet-apps — tarda unos minutos"
  subred=$(az network vnet subnet show -g "$GRUPO" --vnet-name "$VNET" -n snet-apps --query id -o tsv)
  if ! az containerapp env show -g "$GRUPO" -n "$ENTORNO" -o none 2>/dev/null; then
    az containerapp env create -g "$GRUPO" -n "$ENTORNO" -l "$UBICACION" \
      --infrastructure-subnet-resource-id "$subred" --internal-only false \
      --logs-workspace-id "$ws_id" --logs-workspace-key "$ws_clave" --tags $ETIQUETAS -o none
  fi
  guardar ENTORNO_ID "$(az containerapp env show -g "$GRUPO" -n "$ENTORNO" --query id -o tsv)"
  echo "Entorno listo."
  ;;

dbinit)
  requerir DUENO MYSQL_FQDN CLAVE_ADMIN
  guardar DUENO "$DUENO"
  [ -n "${CLAVE_INGESTA:-}" ] || guardar CLAVE_INGESTA "$(clave_nueva)"
  [ -n "${CLAVE_API:-}" ] || guardar CLAVE_API "$(clave_nueva)"
  titulo "Trabajo db-init"
  if az containerapp job show -g "$GRUPO" -n db-init -o none 2>/dev/null; then
    az containerapp job secret set -g "$GRUPO" -n db-init \
      --secrets "admin-pw=$CLAVE_ADMIN" "ingesta-pw=$CLAVE_INGESTA" "api-pw=$CLAVE_API" -o none
  else
    az containerapp job create -g "$GRUPO" -n db-init --environment "$ENTORNO" \
      --trigger-type Manual --replica-timeout 300 --replica-retry-limit 1 \
      --replica-completion-count 1 --parallelism 1 \
      --image "ghcr.io/${DUENO}/securechain-db-init:latest" --cpu 0.25 --memory 0.5Gi \
      --secrets "admin-pw=$CLAVE_ADMIN" "ingesta-pw=$CLAVE_INGESTA" "api-pw=$CLAVE_API" \
      --env-vars "MYSQL_HOST=$MYSQL_FQDN" "MYSQL_DATABASE=$BASE" "MYSQL_ADMIN_USER=$ADMIN_MYSQL" \
        "MYSQL_ADMIN_PASSWORD=secretref:admin-pw" "INGESTA_PASSWORD=secretref:ingesta-pw" \
        "API_PASSWORD=secretref:api-pw" --tags $ETIQUETAS -o none
  fi
  ejecucion=$(az containerapp job start -g "$GRUPO" -n db-init --query name -o tsv)
  echo "Ejecutando $ejecucion..."
  for _ in $(seq 1 60); do
    estado=$(az containerapp job execution show -g "$GRUPO" -n db-init --job-execution-name "$ejecucion" --query properties.status -o tsv)
    case "$estado" in Succeeded) echo "db-init terminó bien: tablas y usuarios listos."; exit 0;;
                      Failed) echo "db-init falló. Revisa sus logs en el portal: Container Apps Jobs → db-init → Historial de ejecuciones" >&2; exit 1;; esac
    sleep 5
  done
  echo "db-init sigue corriendo; revisa más tarde con el paso 'estado'."
  ;;

binance)
  requerir DUENO ENTORNO_ID MYSQL_FQDN CLAVE_INGESTA
  titulo "Contenedor binance-ws (1 réplica fija, 0,25 vCPU / 0,5 GiB)"
  export UBICACION ENTORNO_ID DUENO MYSQL_FQDN CLAVE_INGESTA BASE SIMBOLOS
  archivo=$(renderizar deploy/binance-ws.yaml)
  if az containerapp show -g "$GRUPO" -n binance-ws -o none 2>/dev/null; then
    az containerapp update -g "$GRUPO" -n binance-ws --yaml "$archivo" -o none
  else
    az containerapp create -g "$GRUPO" -n binance-ws --yaml "$archivo" -o none
  fi
  rm -f "$archivo"
  echo "Desplegado. Logs en vivo: az containerapp logs show -g $GRUPO -n binance-ws --follow"
  ;;

web)
  requerir UBICACION
  [ -n "${CUENTA_WEB:-}" ] || guardar CUENTA_WEB "stsecurechain$(tr -dc 'a-z0-9' < /dev/urandom | head -c 8)"
  titulo "Storage Account $CUENTA_WEB con sitio estático"
  az storage account create -g "$GRUPO" -n "$CUENTA_WEB" -l "$UBICACION" --sku Standard_LRS --kind StorageV2 \
    --min-tls-version TLS1_2 --allow-blob-public-access false --https-only true --tags $ETIQUETAS -o none
  az storage blob service-properties update --account-name "$CUENTA_WEB" --static-website true \
    --index-document index.html --404-document index.html -o none
  guardar URL_WEB "$(az storage account show -g "$GRUPO" -n "$CUENTA_WEB" --query primaryEndpoints.web -o tsv | sed 's:/*$::')"
  echo "Sitio estático: $URL_WEB"
  ;;

api)
  requerir DUENO ENTORNO_ID MYSQL_FQDN CLAVE_API URL_WEB
  titulo "Contenedor api-flask (HTTPS, CORS solo para $URL_WEB, escala de 0 a 2)"
  export UBICACION ENTORNO_ID DUENO MYSQL_FQDN CLAVE_API BASE URL_WEB
  archivo=$(renderizar deploy/api-flask.yaml)
  if az containerapp show -g "$GRUPO" -n api-flask -o none 2>/dev/null; then
    az containerapp update -g "$GRUPO" -n api-flask --yaml "$archivo" -o none
  else
    az containerapp create -g "$GRUPO" -n api-flask --yaml "$archivo" -o none
  fi
  rm -f "$archivo"
  guardar URL_API "https://$(az containerapp show -g "$GRUPO" -n api-flask --query properties.configuration.ingress.fqdn -o tsv)"
  echo "API: $URL_API/api/precios"
  ;;

subir-web)
  requerir CUENTA_WEB URL_API
  titulo "Subiendo el frontend a $CUENTA_WEB"
  tmp=$(mktemp -d)
  cp -r web/. "$tmp/"
  printf 'window.SECURECHAIN_API = "%s";\n' "$URL_API" > "$tmp/config.js"
  az storage blob upload-batch --account-name "$CUENTA_WEB" -s "$tmp" -d '$web' --overwrite -o none
  rm -rf "$tmp"
  echo "Listo: $URL_WEB"
  ;;

estado)
  titulo "Recursos en $GRUPO"
  az resource list -g "$GRUPO" --query "[].{nombre:name, tipo:type}" -o table
  titulo "Contenedores"
  az containerapp list -g "$GRUPO" --query "[].{app:name, estado:properties.runningStatus, revision:properties.latestRevisionName}" -o table
  if [ -n "${URL_API:-}" ]; then
    titulo "Prueba de la API"
    curl -fsS "$URL_API/readyz" && echo
    curl -fsS "$URL_API/api/precios" | head -c 600 && echo
  fi
  [ -n "${URL_WEB:-}" ] && echo "Frontend: $URL_WEB"
  ;;

*)
  sed -n '2,22p' "$0"
  exit 1
  ;;
esac
