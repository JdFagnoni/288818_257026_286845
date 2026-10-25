#!/bin/bash
# Script de despliegue para Kubernetes
# Uso: ./apply-k8s.sh

set -e

echo "=== Desplegando PharmaGo en Kubernetes ==="

# Verificar que kubectl está disponible
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl no está instalado o no está en el PATH"
    exit 1
fi

# Verificar que el contexto de kubectl está conectado
if ! kubectl get nodes --request-timeout=10s >/dev/null 2>&1; then
    echo "Error: kubectl no puede acceder a un cluster Kubernetes."
    echo "Verifica el contexto actual con: kubectl config current-context"
    exit 1
fi

backend_count=$(kubectl get nodes -l node-type=backend --no-headers 2>/dev/null | awk '$2 == "Ready" {count++} END {print count+0}')
ops_count=$(kubectl get nodes -l node-type=ops --no-headers 2>/dev/null | awk '$2 == "Ready" {count++} END {print count+0}')
if [ "$backend_count" -lt 3 ] || [ "$ops_count" -ne 1 ]; then
    echo "Error: node pools are not configured for this deployment."
    echo "Need at least 3 Ready nodes labeled node-type=backend and exactly 1 Ready node labeled node-type=ops."
    echo "Run ./configure-nodes.sh OPS_NODE GATEWAY_NODE USERS_NODE PHARMACY_NODE"
    echo "See README-k8s.md for the topology and node requirements."
    exit 1
fi

echo ""
echo "1. Creando namespace..."
kubectl apply -f namespace.yaml

echo ""
echo "2. Creando secrets..."
kubectl apply -f secrets/db-secret.yaml

echo ""
echo "3. Creando configmaps..."
kubectl apply -f configmaps/prometheus-config.yaml
kubectl apply -f configmaps/otel-collector-config.yaml
kubectl apply -f configmaps/grafana-provisioning.yaml
kubectl apply -f configmaps/grafana-dashboards.yaml
kubectl apply -f configmaps/grafana-dashboard-infra.yaml
kubectl apply -f configmaps/grafana-alerts.yaml
kubectl apply -f configmaps/fluent-bit-config.yaml

echo ""
echo "4. Creando StorageClass y PersistentVolumes..."
kubectl apply -f persistent-volumes/storage-class.yaml

# Eliminar solo PVs en estado Released (huérfanos tras borrar el namespace).
# No intentar borrar PVs Bound: kubectl delete se bloquearía indefinidamente.
echo "   Limpiando PVs huérfanos (Released)..."
for pv in sql-pv elasticsearch-pv prometheus-pv grafana-pv; do
  status=$(kubectl get pv $pv -o jsonpath='{.status.phase}' 2>/dev/null || echo "NotFound")
  if [ "$status" = "Released" ] || [ "$status" = "Failed" ]; then
    kubectl delete pv $pv --ignore-not-found=true 2>/dev/null || true
  fi
done
sleep 2

kubectl apply -f persistent-volumes/sql-pv.yaml
kubectl apply -f persistent-volumes/elasticsearch-pv.yaml
kubectl apply -f persistent-volumes/prometheus-pv.yaml
kubectl apply -f persistent-volumes/grafana-pv.yaml

echo ""
echo "5. Desplegando base de datos..."
kubectl apply -f services/ops/db-service.yaml
kubectl apply -f deployments/ops/db-deployment.yaml

echo ""
echo "   Esperando a que la base de datos esté lista..."
timeout=0
max_timeout=300
while [ $timeout -lt $max_timeout ]; do
    pod_ready=$(kubectl get pod -l app=pharmago-db -n pharmago -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
    if [ "$pod_ready" = "True" ]; then
        echo "   Base de datos lista!"
        break
    fi
    sleep 5
    timeout=$((timeout + 5))
    echo "   Esperando... ($timeout/$max_timeout segundos)"
done
if [ $timeout -ge $max_timeout ]; then
    echo "   Timeout esperando la base de datos. Continuando..."
fi

echo ""
echo "6. Desplegando servicios de observabilidad..."
# Elasticsearch primero
kubectl apply -f services/ops/elasticsearch-service.yaml
kubectl apply -f deployments/ops/elasticsearch-deployment.yaml

# Esperar a que Elasticsearch esté listo
echo "   Esperando a que Elasticsearch esté listo..."
timeout=0
max_timeout=300
while [ $timeout -lt $max_timeout ]; do
    pod_ready=$(kubectl get pod -l app=elasticsearch -n pharmago -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
    if [ "$pod_ready" = "True" ]; then
        echo "   Elasticsearch listo!"
        break
    fi
    sleep 5
    timeout=$((timeout + 5))
    echo "   Esperando... ($timeout/$max_timeout segundos)"
done
if [ $timeout -ge $max_timeout ]; then
    echo "   Timeout esperando Elasticsearch. Continuando..."
fi

# Resto de servicios ops
kubectl apply -f services/ops/otel-collector-service.yaml
kubectl apply -f services/ops/jaeger-service.yaml
kubectl apply -f deployments/ops/jaeger-deployment.yaml
kubectl apply -f deployments/ops/prometheus-serviceaccount.yaml
kubectl apply -f deployments/ops/prometheus-clusterrole.yaml
kubectl apply -f deployments/ops/prometheus-clusterrolebinding.yaml
kubectl apply -f services/ops/prometheus-service.yaml
kubectl apply -f deployments/ops/prometheus-deployment.yaml
kubectl apply -f deployments/ops/otel-collector-serviceaccount.yaml
kubectl apply -f deployments/ops/otel-collector-clusterrole.yaml
kubectl apply -f deployments/ops/otel-collector-clusterrolebinding.yaml
kubectl apply -f deployments/ops/otel-collector-deployment.yaml

kubectl apply -f services/ops/node-exporter-service.yaml
kubectl apply -f deployments/ops/node-exporter-daemonset.yaml

kubectl apply -f services/ops/grafana-service.yaml
kubectl apply -f deployments/ops/grafana-deployment.yaml

kubectl apply -f services/ops/kibana-service.yaml
kubectl apply -f deployments/ops/kibana-deployment.yaml

# Fluent Bit: recolecta logs de pods y los envía a Elasticsearch (pharmago-logs-*)
kubectl apply -f deployments/ops/fluent-bit-serviceaccount.yaml
kubectl apply -f deployments/ops/fluent-bit-clusterrole.yaml
kubectl apply -f deployments/ops/fluent-bit-clusterrolebinding.yaml
kubectl apply -f deployments/ops/fluent-bit-daemonset.yaml

echo ""
echo "7. Desplegando servicios backend..."
kubectl apply -f services/backend/users-service-service.yaml
kubectl apply -f deployments/backend/users-service-deployment.yaml

kubectl apply -f services/backend/pharmacy-service-service.yaml
kubectl apply -f deployments/backend/pharmacy-service-deployment.yaml

kubectl apply -f services/backend/api-gateway-service.yaml
kubectl apply -f deployments/backend/api-gateway-deployment.yaml

echo ""
echo "8. Desplegando frontend..."
kubectl apply -f services/frontend/ui-service.yaml
kubectl apply -f deployments/frontend/ui-deployment.yaml

echo ""
echo "=== Despliegue completado ==="
echo ""
echo "Verificando estado de los pods..."
kubectl get pods -n pharmago -o wide

echo ""
echo "Para acceder a los servicios:"
echo "  ./port-forward.sh"
echo ""
if command -v minikube >/dev/null 2>&1; then
    echo "O con minikube service:"
    echo "  Frontend:     minikube service pharmago-ui -n pharmago --url"
    echo "  Grafana:      minikube service grafana -n pharmago --url"
    echo "  Kibana:       minikube service kibana -n pharmago --url"
    echo "  Prometheus:   minikube service prometheus -n pharmago --url"
fi

