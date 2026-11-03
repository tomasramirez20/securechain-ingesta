#!/usr/bin/env bash
# SecureChain · modo ahorro mientras el proyecto se presenta y se aprueba.
#   ./deploy/ahorro.sh alertas   Alertas de presupuesto: 50, 80 y 100 % del crédito (y pronóstico)
#   ./deploy/ahorro.sh apagar    Detiene la ingesta continua y limita los logs (modo ahorro)
#   ./deploy/ahorro.sh demo      Enciende la ingesta y precalienta la API antes de presentar
#   ./deploy/ahorro.sh estado    Qué está encendido ahora
set -euo pipefail

GRUPO=${GRUPO:-rg-securechain}
CREDITO=${CREDITO:-100}
LOGS=${LOGS:-log-securechain}

case "${1:-}" in

alertas)
  sub=$(az account show --query id -o tsv)
  correo=$(az account show --query user.name -o tsv)
  correo=${correo##*#}  # las cuentas personales llegan como live.com#usuario@dominio
  inicio=$(date -u +%Y-%m-01T00:00:00Z)
  fin=$(date -u -d "$(date -u +%Y-%m-01) +12 months" +%Y-%m-01T00:00:00Z)
  cuerpo=$(mktemp)
  python3 - "$CREDITO" "$inicio" "$fin" "$correo" > "$cuerpo" <<'PY'
import json, sys
monto, inicio, fin, correo = float(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
def aviso(umbral, tipo):
    return {"enabled": True, "operator": "GreaterThanOrEqualTo", "threshold": umbral,
            "thresholdType": tipo, "contactEmails": [correo], "contactRoles": ["Owner"]}
print(json.dumps({"properties": {
    "category": "Cost", "amount": monto, "timeGrain": "Annually",
    "timePeriod": {"startDate": inicio, "endDate": fin},
    "notifications": {"real50": aviso(50, "Actual"), "real80": aviso(80, "Actual"),
                      "real100": aviso(100, "Actual"), "pronostico100": aviso(100, "Forecasted")}}}))
PY
  az rest --method put \
    --url "https://management.azure.com/subscriptions/$sub/providers/Microsoft.Consumption/budgets/securechain-creditos?api-version=2023-05-01" \
    --body @"$cuerpo" -o none
  rm -f "$cuerpo"
  echo "Alertas listas: correo a $correo al llegar al 50, 80 y 100 % de $CREDITO USD, y si el pronóstico anual supera el 100 %."
  ;;

apagar)
  for r in $(az containerapp revision list -g "$GRUPO" -n binance-ws --query "[?properties.active].name" -o tsv); do
    az containerapp revision deactivate -g "$GRUPO" -n binance-ws --revision "$r" -o none
  done
  # Tope diario de 0,1 GB para Log Analytics: evita sorpresas si algo empieza a escribir logs de más.
  az monitor log-analytics workspace update -g "$GRUPO" -n "$LOGS" --quota 0.1 -o none
  echo "Modo ahorro activo: binance-ws detenido, sin consumo."
  echo "MySQL conserva los últimos precios; api-flask se apaga sola sin tráfico y el sitio sigue en línea."
  ;;

demo)
  r=$(az containerapp show -g "$GRUPO" -n binance-ws --query properties.latestRevisionName -o tsv)
  az containerapp revision activate -g "$GRUPO" -n binance-ws --revision "$r" -o none
  url=$(az containerapp show -g "$GRUPO" -n api-flask --query properties.configuration.ingress.fqdn -o tsv)
  echo "Ingesta encendida. Esperando precios en vivo (suele tardar 1 a 2 minutos)..."
  # Se da por lista cuando el precio más reciente tiene menos de 60 s; esto también
  # despierta a api-flask, que estaba en 0 réplicas.
  for _ in $(seq 1 36); do
    edad=$(curl -fsS -m 20 "https://$url/api/precios" 2>/dev/null | python3 -c '
import datetime as d, json, sys
datos = json.load(sys.stdin)["datos"]
ultimo = max(d.datetime.fromisoformat(x["evento_ts"].replace("Z", "+00:00")) for x in datos)
print(int((d.datetime.now(d.timezone.utc) - ultimo).total_seconds()))' 2>/dev/null || echo 9999)
    if [ "$edad" -lt 60 ]; then
      echo "Precios en vivo: el último dato tiene ${edad} s. Listo para presentar."
      echo "Al terminar la junta: ./deploy/ahorro.sh apagar"
      exit 0
    fi
    sleep 5
  done
  echo "La ingesta no entregó precios nuevos a tiempo. Revisa: az containerapp logs show -g $GRUPO -n binance-ws --tail 20" >&2
  exit 1
  ;;

estado)
  for app in binance-ws api-flask; do
    echo "== $app"
    az containerapp revision list -g "$GRUPO" -n "$app" \
      --query "[].{revision:name, activa:properties.active, replicas:properties.replicas}" -o table
  done
  echo "== MySQL"
  az mysql flexible-server list -g "$GRUPO" --query "[].{servidor:name, estado:state, sku:sku.name}" -o table
  echo "Crédito restante: portal → Education → Información general."
  ;;

*)
  sed -n '2,6p' "$0"
  exit 1
  ;;
esac
#!/usr/bin/env bash
# SecureChain · modo ahorro mientras el proyecto se presenta y se aprueba.
#   ./deploy/ahorro.sh alertas   Alertas de presupuesto: 50, 80 y 100 % del crédito (y pronóstico)
#   ./deploy/ahorro.sh apagar    Detiene la ingesta continua y limita los logs (modo ahorro)
#   ./deploy/ahorro.sh demo      Enciende la ingesta y precalienta la API antes de presentar
#   ./deploy/ahorro.sh estado    Qué está encendido ahora
set -euo pipefail

GRUPO=${GRUPO:-rg-securechain}
CREDITO=${CREDITO:-100}
LOGS=${LOGS:-log-securechain}

case "${1:-}" in

alertas)
  sub=$(az account show --query id -o tsv)
  correo=$(az account show --query user.name -o tsv)
  inicio=$(date -u +%Y-%m-01T00:00:00Z)
  fin=$(date -u -d "$(date -u +%Y-%m-01) +12 months" +%Y-%m-01T00:00:00Z)
  cuerpo=$(mktemp)
  python3 - "$CREDITO" "$inicio" "$fin" "$correo" > "$cuerpo" <<'PY'
import json, sys
monto, inicio, fin, correo = float(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
def aviso(umbral, tipo):
    return {"enabled": True, "operator": "GreaterThanOrEqualTo", "threshold": umbral,
            "thresholdType": tipo, "contactEmails": [correo], "contactRoles": ["Owner"]}
print(json.dumps({"properties": {
    "category": "Cost", "amount": monto, "timeGrain": "Annually",
    "timePeriod": {"startDate": inicio, "endDate": fin},
    "notifications": {"real50": aviso(50, "Actual"), "real80": aviso(80, "Actual"),
                      "real100": aviso(100, "Actual"), "pronostico100": aviso(100, "Forecasted")}}}))
PY
  az rest --method put \
    --url "https://management.azure.com/subscriptions/$sub/providers/Microsoft.Consumption/budgets/securechain-creditos?api-version=2023-05-01" \
    --body @"$cuerpo" -o none
  rm -f "$cuerpo"
  echo "Alertas listas: correo a $correo al llegar al 50, 80 y 100 % de $CREDITO USD, y si el pronóstico anual supera el 100 %."
  ;;

apagar)
  for r in $(az containerapp revision list -g "$GRUPO" -n binance-ws --query "[?properties.active].name" -o tsv); do
    az containerapp revision deactivate -g "$GRUPO" -n binance-ws --revision "$r" -o none
  done
  # Tope diario de 0,1 GB para Log Analytics: evita sorpresas si algo empieza a escribir logs de más.
  az monitor log-analytics workspace update -g "$GRUPO" -n "$LOGS" --quota 0.1 -o none
  echo "Modo ahorro activo: binance-ws detenido, sin consumo."
  echo "MySQL conserva los últimos precios; api-flask se apaga sola sin tráfico y el sitio sigue en línea."
  ;;

demo)
  r=$(az containerapp show -g "$GRUPO" -n binance-ws --query properties.latestRevisionName -o tsv)
  az containerapp revision activate -g "$GRUPO" -n binance-ws --revision "$r" -o none
  url=$(az containerapp show -g "$GRUPO" -n api-flask --query properties.configuration.ingress.fqdn -o tsv)
  echo "Ingesta encendida. Precalentando la API (la primera visita tras el reposo tarda unos segundos)..."
  for _ in $(seq 1 12); do
    curl -fsS -m 20 "https://$url/readyz" > /dev/null 2>&1 && break
    sleep 5
  done
  sleep 15
  curl -fsS "https://$url/api/precios" | head -c 300; echo
  echo "Listo para presentar. Al terminar la junta: ./deploy/ahorro.sh apagar"
  ;;

estado)
  for app in binance-ws api-flask; do
    echo "== $app"
    az containerapp revision list -g "$GRUPO" -n "$app" \
      --query "[].{revision:name, activa:properties.active, replicas:properties.replicas}" -o table
  done
  echo "== MySQL"
  az mysql flexible-server list -g "$GRUPO" --query "[].{servidor:name, estado:state, sku:sku.name}" -o table
  echo "Crédito restante: portal → Education → Información general."
  ;;

*)
  sed -n '2,6p' "$0"
  exit 1
  ;;
esac
