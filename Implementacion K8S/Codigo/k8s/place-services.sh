#!/usr/bin/env bash
# Place each backend microservice on its reference node:
#   users-service -> pharmago, api-gateway and UI -> pharmago-m03,
#   pharmacy-service -> pharmago-m04.
# The manifests only guarantee that the microservices land on different backend
# nodes; this script makes the placement match the team's reference topology by
# temporarily cordoning the nodes a service must not use. Run it once after the
# first deployment: it scales the services to zero, so the app is down ~2 min.
# Usage: ./place-services.sh

set -euo pipefail

NAMESPACE=pharmago
USERS_NODE=pharmago
GATEWAY_NODE=pharmago-m03
PHARMACY_NODE=pharmago-m04

uncordon_all() {
  kubectl uncordon "$USERS_NODE" "$GATEWAY_NODE" "$PHARMACY_NODE" >/dev/null 2>&1 || true
}
trap uncordon_all EXIT

scale_and_wait() {
  kubectl scale "deployment/$1" -n "$NAMESPACE" --replicas="$2" >/dev/null
  kubectl rollout status "deployment/$1" -n "$NAMESPACE" --timeout=300s
}

for node in "$USERS_NODE" "$GATEWAY_NODE" "$PHARMACY_NODE"; do
  if [ "$(kubectl get node "$node" -o jsonpath='{.metadata.labels.node-type}')" != "backend" ]; then
    echo "Error: node '$node' is not labeled node-type=backend. Run configure-nodes.sh first." >&2
    exit 1
  fi
done

echo "Scaling the backend microservices and the UI to zero..."
for app in pharmago-api-gateway pharmago-users-service pharmago-pharmacy-service pharmago-ui; do
  kubectl scale "deployment/$app" -n "$NAMESPACE" --replicas=0 >/dev/null
done
for app in pharmago-api-gateway pharmago-users-service pharmago-pharmacy-service pharmago-ui; do
  kubectl wait --for=delete pod -l "app=$app" -n "$NAMESPACE" --timeout=180s >/dev/null 2>&1 || true
done

echo "users-service -> $USERS_NODE"
kubectl cordon "$GATEWAY_NODE" "$PHARMACY_NODE" >/dev/null
scale_and_wait pharmago-users-service 2

echo "api-gateway and UI -> $GATEWAY_NODE"
kubectl cordon "$USERS_NODE" >/dev/null
kubectl uncordon "$GATEWAY_NODE" >/dev/null
scale_and_wait pharmago-api-gateway 2
scale_and_wait pharmago-ui 1

echo "pharmacy-service -> $PHARMACY_NODE"
kubectl uncordon "$PHARMACY_NODE" >/dev/null
scale_and_wait pharmago-pharmacy-service 2

uncordon_all
echo ""
kubectl get pods -n "$NAMESPACE" -l 'app in (pharmago-api-gateway,pharmago-users-service,pharmago-pharmacy-service,pharmago-ui)' -o wide
