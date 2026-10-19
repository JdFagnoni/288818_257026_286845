#!/usr/bin/env bash
# Verifica que reemplazar los microservicios backend no pierde requests.
# Lanza un pod dentro del cluster que consulta el gateway sin parar, reinicia
# los deployments (igual que al desplegar una versión nueva) y cuenta los
# requests fallidos. Corre dentro del cluster porque kubectl port-forward se
# ata a un solo pod y se corta cuando ese pod se reemplaza.
#
# Uso: ./test-zero-downtime.sh [deployment ...]
#   Sin argumentos reinicia gateway, users y pharmacy, uno tras otro.
#   INTERVAL=0.7 ./test-zero-downtime.sh   segundos entre requests

set -euo pipefail

NAMESPACE=pharmago
PROBE_POD=zero-downtime-probe
GATEWAY_URL=http://pharmago-api-gateway
# El gateway limita a 100 requests/min y 1000/hora por IP. Con 0.7s entre
# requests (~85/min) una corrida completa usa ~250 del cupo horario.
INTERVAL="${INTERVAL:-0.7}"

if [ "$#" -gt 0 ]; then
  deployments=("$@")
else
  deployments=(pharmago-api-gateway pharmago-users-service pharmago-pharmacy-service)
fi

cleanup() {
  kubectl delete pod "$PROBE_POD" -n "$NAMESPACE" --ignore-not-found --wait=false >/dev/null
}
trap cleanup EXIT

kubectl delete pod "$PROBE_POD" -n "$NAMESPACE" --ignore-not-found --wait=true >/dev/null

# Alterna tres rutas: el gateway solo, gateway -> users, y gateway -> pharmacy -> BD.
# busybox:1.36 ya está en el nodo ops (lo usan los initContainers).
probe_loop='
i=0
while true; do
  case $((i % 3)) in
    0) path=/health ;;
    1) path=/test-health ;;
    2) path=/api/drug ;;
  esac
  if wget -q -T 3 -O /dev/null "$GATEWAY_URL$path"; then result=ok; else result=FAIL; fi
  echo "$(date +%H:%M:%S) $result $path"
  i=$((i + 1))
  sleep "$INTERVAL"
done'

echo "=== Prueba de despliegue sin caída ==="
echo "Lanzando el pod de prueba..."
kubectl run "$PROBE_POD" -n "$NAMESPACE" --image=busybox:1.36 --image-pull-policy=IfNotPresent \
  --restart=Never --env="GATEWAY_URL=$GATEWAY_URL" --env="INTERVAL=$INTERVAL" \
  --overrides='{"spec":{"nodeSelector":{"node-type":"ops"}}}' \
  -- sh -c "$probe_loop" >/dev/null
kubectl wait --for=condition=Ready "pod/$PROBE_POD" -n "$NAMESPACE" --timeout=60s >/dev/null

echo "Línea de base (10s)..."
sleep 10

for deployment in "${deployments[@]}"; do
  echo ""
  echo "$(date +%H:%M:%S) Reemplazando $deployment..."
  kubectl rollout restart "deployment/$deployment" -n "$NAMESPACE" >/dev/null
  kubectl rollout status "deployment/$deployment" -n "$NAMESPACE" --timeout=300s
done

echo ""
echo "Cola (10s)..."
sleep 10

results="$(kubectl logs "$PROBE_POD" -n "$NAMESPACE")"
total=$(grep -cE ' (ok|FAIL) ' <<< "$results" || true)
failed=$(grep -c ' FAIL ' <<< "$results" || true)

echo ""
echo "=== Resultado ==="
echo "Requests:  $total"
echo "Fallidos:  $failed"
if [ "$total" -gt 0 ]; then
  awk -v t="$total" -v f="$failed" 'BEGIN { printf "Disponibilidad: %.2f%%\n", (t - f) * 100 / t }'
fi

if [ "$failed" -gt 0 ]; then
  echo ""
  echo "Requests fallidos:"
  grep ' FAIL ' <<< "$results"
  exit 1
fi
