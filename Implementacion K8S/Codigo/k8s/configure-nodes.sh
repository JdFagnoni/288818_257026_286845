#!/usr/bin/env bash
# Assign the node pools used by the PharmaGo Kubernetes manifests.
# Usage: ./configure-nodes.sh OPS_NODE GATEWAY_NODE USERS_NODE PHARMACY_NODE

set -euo pipefail

if [ "$#" -ne 4 ]; then
  echo "Usage: $0 OPS_NODE GATEWAY_NODE USERS_NODE PHARMACY_NODE" >&2
  exit 2
fi

if ! command -v kubectl >/dev/null 2>&1; then
  echo "Error: kubectl is not installed or is not in PATH." >&2
  exit 1
fi

ops_node="$1"
gateway_node="$2"
users_node="$3"
pharmacy_node="$4"

if [ "$ops_node" = "$gateway_node" ] || [ "$ops_node" = "$users_node" ] || \
   [ "$ops_node" = "$pharmacy_node" ] || [ "$gateway_node" = "$users_node" ] || \
   [ "$gateway_node" = "$pharmacy_node" ] || [ "$users_node" = "$pharmacy_node" ]; then
  echo "Error: the ops node and each backend microservice must use different nodes." >&2
  exit 1
fi

for node in "$ops_node" "$gateway_node" "$users_node" "$pharmacy_node"; do
  if ! kubectl get node "$node" >/dev/null 2>&1; then
    echo "Error: node '$node' does not exist in the current kubectl context." >&2
    exit 1
  fi

  ready="$(kubectl get node "$node" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')"
  if [ "$ready" != "True" ]; then
    echo "Error: node '$node' is not Ready." >&2
    exit 1
  fi

  unschedulable="$(kubectl get node "$node" -o jsonpath='{.spec.unschedulable}')"
  if [ "$unschedulable" = "true" ]; then
    echo "Error: node '$node' is cordoned (unschedulable)." >&2
    exit 1
  fi

  taint_effects="$(kubectl get node "$node" -o jsonpath='{range .spec.taints[*]}{.effect}{"\n"}{end}')"
  while IFS= read -r effect; do
    if [ "$effect" = "NoSchedule" ] || [ "$effect" = "NoExecute" ]; then
      echo "Error: node '$node' has a $effect taint, which blocks these workloads." >&2
      exit 1
    fi
  done <<< "$taint_effects"
done

# node-type is reserved for this deployment. Clear stale values so an old
# single-node 'all' or previous role label cannot satisfy a placement rule.
all_nodes="$(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')"
while IFS= read -r node; do
  [ -z "$node" ] && continue
  current_type="$(kubectl get node "$node" -o jsonpath='{.metadata.labels.node-type}')"
  if [ -n "$current_type" ]; then
    kubectl label node "$node" node-type-
  fi
done <<< "$all_nodes"

kubectl label node "$ops_node" node-type=ops --overwrite
kubectl label node "$gateway_node" node-type=backend --overwrite
kubectl label node "$users_node" node-type=backend --overwrite
kubectl label node "$pharmacy_node" node-type=backend --overwrite

echo "Configured node pools:"
kubectl get nodes -L node-type
