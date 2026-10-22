#!/usr/bin/env bash
# Mide el MTTR (tiempo de recuperación) de un microservicio backend: provoca
# una falla en uno de sus pods y cronometra hasta que vuelve a haber un pod
# Ready que lo reemplace.
#
# Uso: ./measure-mttr.sh [deployment] [delete|crash]
#   delete  borra el pod: el ReplicaSet crea uno nuevo (falla del pod o del nodo)
#   crash   termina el proceso dentro del contenedor: kubelet lo reinicia en el
#           mismo pod (caída de la aplicación)
#   RUNS=3 ./measure-mttr.sh   cantidad de mediciones (default 3)
#
# Ejemplo: ./measure-mttr.sh pharmago-users-service crash

set -euo pipefail

NAMESPACE=pharmago
DEPLOYMENT="${1:-pharmago-pharmacy-service}"
MODE="${2:-delete}"
RUNS="${RUNS:-3}"

if [ "$MODE" != "delete" ] && [ "$MODE" != "crash" ]; then
  echo "Modo inválido: $MODE (usar delete o crash)" >&2
  exit 2
fi

app_label="$(kubectl get deployment "$DEPLOYMENT" -n "$NAMESPACE" -o jsonpath='{.spec.selector.matchLabels.app}')"

now() { date +%s.%N; }

# Pods Ready del deployment, uno por línea: "nombre reinicios"
ready_pods() {
  kubectl get pods -n "$NAMESPACE" -l "app=$app_label" \
    -o jsonpath='{range .items[*]}{.metadata.name} {.status.containerStatuses[0].restartCount} {.status.conditions[?(@.type=="Ready")].status} {.metadata.deletionTimestamp}{"\n"}{end}' \
    | awk '$3 == "True" && $4 == "" { print $1, $2 }'
}

echo "=== MTTR de $DEPLOYMENT (modo: $MODE, $RUNS mediciones) ==="
results=()

for run in $(seq 1 "$RUNS"); do
  kubectl rollout status "deployment/$DEPLOYMENT" -n "$NAMESPACE" --timeout=300s >/dev/null
  before="$(ready_pods)"
  # El pod con menos reinicios, para que el backoff de kubelet tras caídas
  # repetidas (10s, 20s, 40s...) no infle la medición en modo crash.
  read -r victim restarts <<< "$(sort -k2 -n <<< "$before" | head -1)"

  echo ""
  echo "[$run/$RUNS] $(date +%H:%M:%S) Falla en $victim ($MODE)..."
  t0=$(now)
  if [ "$MODE" = "delete" ]; then
    kubectl delete pod "$victim" -n "$NAMESPACE" --wait=false >/dev/null
  else
    kubectl exec "$victim" -n "$NAMESPACE" -- sh -c 'kill 1' >/dev/null 2>&1 || true
  fi

  # delete: recuperado cuando hay un pod Ready que no existía antes.
  # crash: recuperado cuando el mismo pod volvió a Ready con un reinicio más.
  # La pausa inicial evita leer el estado previo antes de que kubelet registre la caída.
  sleep 1
  while true; do
    current="$(ready_pods)"
    if [ "$MODE" = "delete" ]; then
      recovered=$(awk 'NR == FNR { old[$1] = 1; next } !($1 in old)' <(echo "$before") <(echo "$current") | head -1)
    else
      recovered=$(awk -v pod="$victim" -v r="$restarts" '$1 == pod && $2 > r' <<< "$current")
    fi
    [ -n "$recovered" ] && break
    sleep 0.5
  done
  t1=$(now)

  mttr=$(awk -v a="$t0" -v b="$t1" 'BEGIN { printf "%.1f", b - a }')
  results+=("$mttr")
  echo "[$run/$RUNS] $(date +%H:%M:%S) Recuperado: $(cut -d' ' -f1 <<< "$recovered") Ready en ${mttr}s"
  sleep 5
done

echo ""
echo "=== Resultado ==="
printf '%s\n' "${results[@]}" | awk -v d="$DEPLOYMENT" -v m="$MODE" '
  { sum += $1; if (min == "" || $1 < min) min = $1; if ($1 > max) max = $1; n++ }
  END { printf "%s (%s): MTTR promedio %.1fs (mín %.1fs, máx %.1fs, %d mediciones)\n", d, m, sum / n, min, max, n }'
echo "La precisión es de ~1s: el script consulta el estado cada 0.5s vía kubectl."
