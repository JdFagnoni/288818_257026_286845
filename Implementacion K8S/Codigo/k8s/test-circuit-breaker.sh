#!/usr/bin/env bash
# Muestra el circuit breaker del gateway en acción. Lanza un pod dentro del
# cluster que consulta /api/drug sin parar, baja pharmacy-service a 0 réplicas,
# lo vuelve a levantar y resume qué respondió el gateway en cada etapa, junto
# con los logs del circuit breaker (abierto / semiabierto / cerrado).
#
# Uso: ./test-circuit-breaker.sh
#   OUTAGE_SECONDS=40 ./test-circuit-breaker.sh   tiempo con pharmacy caído
#   INTERVAL=0.7 ./test-circuit-breaker.sh        segundos entre requests

set -euo pipefail

NAMESPACE=pharmago
PROBE_POD=circuit-breaker-probe
TARGET_DEPLOYMENT=pharmago-pharmacy-service
GATEWAY_URL=http://pharmago-api-gateway
# El gateway limita a 100 requests/min por IP: con 0.7s entre requests (~85/min)
# la prueba no dispara el rate limiting.
INTERVAL="${INTERVAL:-0.7}"
# 40s cubre la apertura del circuito y varios intentos semiabiertos fallidos
# (uno cada 5s) antes de restaurar el servicio.
OUTAGE_SECONDS="${OUTAGE_SECONDS:-40}"

# El pod de prueba registra la hora con la zona horaria de esta máquina, para
# poder compararla con los tiempos del script y cruzarla con Grafana. busybox no
# trae tzdata, así que se pasa como TZ POSIX (el signo va invertido: -0300 -> LOC+03:00).
offset="$(date +%z)"
if [ "${offset:0:1}" = "-" ]; then tz_sign="+"; else tz_sign="-"; fi
PROBE_TZ="LOC${tz_sign}${offset:1:2}:${offset:3:2}"

original_replicas="$(kubectl get deployment "$TARGET_DEPLOYMENT" -n "$NAMESPACE" -o jsonpath='{.spec.replicas}')"

cleanup() {
  kubectl delete pod "$PROBE_POD" -n "$NAMESPACE" --ignore-not-found --wait=false >/dev/null
  # Si la prueba se corta a mitad, no dejar pharmacy apagado.
  kubectl scale deployment "$TARGET_DEPLOYMENT" -n "$NAMESPACE" --replicas="$original_replicas" >/dev/null
}
trap cleanup EXIT

kubectl delete pod "$PROBE_POD" -n "$NAMESPACE" --ignore-not-found --wait=true >/dev/null

# Registra hora y código HTTP de cada request. ERR = no hubo respuesta HTTP en
# 5s. (El wget de busybox no muestra los headers de respuestas con error, por
# eso no se lee X-Circuit-Breaker: el 503 alcanza para identificar el fallback.)
probe_loop='
while true; do
  out=$(wget -S -q -T 5 -O /dev/null "$GATEWAY_URL/api/drug" 2>&1)
  code=$(echo "$out" | grep -o "HTTP/[0-9.]* [0-9]*" | tail -1 | cut -d" " -f2)
  [ -z "$code" ] && code=ERR
  echo "$(date +%H:%M:%S) $code"
  sleep "$INTERVAL"
done'

echo "=== Prueba del circuit breaker ==="
echo "Lanzando el pod de prueba..."
kubectl run "$PROBE_POD" -n "$NAMESPACE" --image=busybox:1.36 --image-pull-policy=IfNotPresent \
  --restart=Never --env="GATEWAY_URL=$GATEWAY_URL" --env="INTERVAL=$INTERVAL" --env="TZ=$PROBE_TZ" \
  --overrides='{"spec":{"nodeSelector":{"node-type":"ops"}}}' \
  -- sh -c "$probe_loop" >/dev/null
kubectl wait --for=condition=Ready "pod/$PROBE_POD" -n "$NAMESPACE" --timeout=60s >/dev/null
start_epoch=$(date +%s)

echo "$(date +%H:%M:%S) Línea de base (10s)..."
sleep 10

echo "$(date +%H:%M:%S) Bajando $TARGET_DEPLOYMENT a 0 réplicas..."
outage_start=$(date +%H:%M:%S)
kubectl scale deployment "$TARGET_DEPLOYMENT" -n "$NAMESPACE" --replicas=0 >/dev/null
sleep "$OUTAGE_SECONDS"

echo "$(date +%H:%M:%S) Restaurando $TARGET_DEPLOYMENT a $original_replicas réplicas..."
recovery_start=$(date +%H:%M:%S)
kubectl scale deployment "$TARGET_DEPLOYMENT" -n "$NAMESPACE" --replicas="$original_replicas" >/dev/null
kubectl rollout status "deployment/$TARGET_DEPLOYMENT" -n "$NAMESPACE" --timeout=300s >/dev/null
echo "$(date +%H:%M:%S) $TARGET_DEPLOYMENT listo. Esperando que el circuito se cierre (15s)..."
sleep 15

results="$(kubectl logs "$PROBE_POD" -n "$NAMESPACE")"

summarize() {
  awk -v from="$1" -v to="$2" '$1 >= from && $1 < to { $1 = ""; sub(/^ /, ""); count[$0]++ }
    END { for (k in count) printf "  %-24s %d\n", k, count[k] }' <<< "$results" | sort
}

echo ""
echo "=== Respuestas del gateway ==="
echo "Antes de la caída:"
summarize "00:00:00" "$outage_start"
echo "Con $TARGET_DEPLOYMENT caído ($outage_start → $recovery_start):"
summarize "$outage_start" "$recovery_start"
echo "Después de restaurarlo:"
summarize "$recovery_start" "99:99:99"
echo ""
echo "  200 = OK · 502/504 = el gateway intentó llamar a pharmacy y falló (circuito cerrado)"
echo "  503 = circuito abierto: el gateway respondió al instante sin llamar a pharmacy"
echo "  ERR = sin respuesta en 5s"

echo ""
echo "=== Logs del circuit breaker (gateway) ==="
since=$(( $(date +%s) - start_epoch + 5 ))
kubectl logs -n "$NAMESPACE" -l app=pharmago-api-gateway --prefix --since="${since}s" --tail=-1 \
  | grep -E "Circuit breaker|Reintento" || echo "  (sin eventos del circuit breaker)"

echo ""
echo "Secuencia completa de requests:"
echo "$results"
