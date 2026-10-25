# Prometheus en el clúster Kubernetes de PharmaGo

La configuración vigente está en [`k8s/README-telemetry.md`](../k8s/README-telemetry.md). Este tutorial se refiere al despliegue de cinco nodos descrito allí.

## Flujo de métricas

```text
Gateway, Users, Pharmacy -- OTLP/gRPC --> OpenTelemetry Collector
cAdvisor de 5 nodos ------ scrape ----> OpenTelemetry Collector
node-exporter de 5 nodos -- scrape ----> OpenTelemetry Collector
Kubelet (storage por pod) --> OpenTelemetry Collector
OpenTelemetry Collector -- OTLP/HTTP --> Prometheus --> Grafana
```

Prometheus recibe las métricas del Collector en su endpoint OTLP/HTTP y solo scrapea su propio endpoint. Las aplicaciones conservan `/metrics` para depuración, pero Prometheus no las scrapea directamente. El Collector consulta cAdvisor en los cinco nodos de referencia y descubre los pods de node-exporter con Kubernetes; no se usa el Service balanceado de node-exporter como único target porque eso perdería la identidad de cuatro de los cinco nodos.

## Consultar las métricas

Desde `Implementacion K8S/Codigo/k8s`:

```bash
./port-forward.sh
```

En `http://127.0.0.1:9090`, probar estas consultas:

```promql
prometheus_http_requests_total{handler="/api/v1/otlp/v1/metrics",code="200"}
count(node_cpu_seconds_total{mode="idle"}) by (node)
container_memory_working_set_bytes{namespace="pharmago"}
rate(container_network_receive_bytes_total{namespace="pharmago"}[5m])
pharmago_http_requests_total
```

El dashboard **PharmaGo - Infra** de Grafana usa CPU, memoria, storage y red de nodo y pod. Los cinco umbrales de alerta están definidos en `k8s/configmaps/grafana-alerts.yaml` y se provisionan automáticamente.

Si el contador de solicitudes OTLP correctas deja de crecer, revisar `kubectl logs -n pharmago -l app=otlp-collector` y `kubectl logs -n pharmago -l app=prometheus`. Si falta un nodo, comprobar `kubectl get pods -n pharmago -l app=node-exporter -o wide` y los permisos del ServiceAccount `otlp-collector`. Tras cambiar un ConfigMap montado con `subPath`, ejecutar `k8s/deploy-telemetry.sh` para reiniciar el componente que lo consume.
