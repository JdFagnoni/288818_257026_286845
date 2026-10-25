#!/usr/bin/env bash
# Actualiza la pila de telemetría en un cluster PharmaGo ya desplegado.
# Construir y cargar las imágenes backend y UI antes de ejecutar este script.
set -euo pipefail

cd "$(dirname "$0")"
namespace=pharmago

kubectl get namespace "$namespace" >/dev/null

for config in otel-collector-config prometheus-config grafana-provisioning grafana-dashboards grafana-dashboard-infra grafana-alerts fluent-bit-config; do
  kubectl apply -f "configmaps/$config.yaml"
done

kubectl apply -f deployments/ops/otel-collector-serviceaccount.yaml
kubectl apply -f deployments/ops/otel-collector-clusterrole.yaml
kubectl apply -f deployments/ops/otel-collector-clusterrolebinding.yaml

kubectl apply -f services/ops/jaeger-service.yaml
kubectl apply -f deployments/ops/jaeger-deployment.yaml
kubectl rollout status deployment/jaeger -n "$namespace" --timeout=300s

kubectl apply -f deployments/ops/prometheus-deployment.yaml
kubectl rollout restart deployment/prometheus -n "$namespace"
kubectl rollout status deployment/prometheus -n "$namespace" --timeout=300s

kubectl apply -f services/ops/otel-collector-service.yaml
kubectl apply -f deployments/ops/otel-collector-deployment.yaml
kubectl rollout restart deployment/otlp-collector -n "$namespace"
kubectl rollout status deployment/otlp-collector -n "$namespace" --timeout=300s

kubectl apply -f deployments/ops/grafana-deployment.yaml
kubectl rollout restart deployment/grafana -n "$namespace"
kubectl rollout status deployment/grafana -n "$namespace" --timeout=300s

kubectl apply -f deployments/ops/fluent-bit-daemonset.yaml
kubectl rollout restart daemonset/fluent-bit -n "$namespace"
kubectl rollout status daemonset/fluent-bit -n "$namespace" --timeout=300s

kubectl apply -f deployments/backend/
for service in pharmago-api-gateway pharmago-users-service pharmago-pharmacy-service; do
  kubectl rollout restart "deployment/$service" -n "$namespace"
  kubectl rollout status "deployment/$service" -n "$namespace" --timeout=300s
done

kubectl rollout restart deployment/pharmago-ui -n "$namespace"
kubectl rollout status deployment/pharmago-ui -n "$namespace" --timeout=300s

kubectl get deployments -n "$namespace"
