# Telemetría de PharmaGo

La pila respeta la topología de cinco nodos de [README-k8s.md](README-k8s.md): los tres microservicios siguen en nodos `backend` distintos y Jaeger, OpenTelemetry Collector, Prometheus, Grafana, Elasticsearch y Kibana quedan en `node-type=ops`. Fluent Bit y node-exporter siguen como DaemonSets en los cinco nodos.

## Recorrido de los datos

| Señal | Origen | Transporte y destino | Consulta |
|---|---|---|---|
| Logs | Consola JSON de gateway, users y pharmacy; accesos JSON de nginx en la UI | Fluent Bit (CRI y Docker) → Elasticsearch | Kibana, data view `pharmago-logs-*` |
| Trazas | ASP.NET Core y HttpClient de los tres microservicios | OTLP/gRPC → Collector → OTLP/gRPC → Jaeger | Jaeger o Explore de Grafana |
| Métricas de aplicación | ASP.NET Core, HttpClient y `PharmaGo.CustomMetrics` | OTLP/gRPC → Collector → OTLP/HTTP → Prometheus | Prometheus y Grafana |
| Métricas de pod/contenedor | cAdvisor y resumen de almacenamiento efímero del kubelet | Collector (receivers Prometheus y kubeletstats) → OTLP/HTTP → Prometheus | Grafana, dashboard Infra |
| Métricas de nodo | node-exporter en cada nodo | Collector (receiver Prometheus) → OTLP/HTTP → Prometheus | Grafana, dashboard Infra |

El Collector consulta cAdvisor de los cinco nodos de referencia mediante el proxy TLS del API server, lee el almacenamiento efímero por pod desde el resumen de cada kubelet y descubre los cinco pods de node-exporter con Kubernetes. Envía todas las métricas a la entrada OTLP/HTTP de Prometheus; Prometheus solo scrapea su propio endpoint. El RBAC del Collector limita la lectura a nodos, pods, métricas y estadísticas de nodo.

Las direcciones de `kubeletstats` corresponden al perfil Minikube de referencia (`192.168.49.2` a `.6`). Si el clúster asigna otras IP internas, actualizarlas en `configmaps/otel-collector-config.yaml` usando `kubectl get nodes -o wide`.

Los logs de .NET salen como una línea JSON por evento con fecha UTC, nivel, categoría, mensaje, estado y scopes. Los logs de negocio incluyen `correlation_id`; cuando existe una actividad OpenTelemetry, también incluyen `trace_id` y `span_id`. Nginx escribe sus accesos como JSON con método, ruta, estado y duración. Fluent Bit quita el prefijo del runtime CRI, añade metadatos de Kubernetes y eleva los campos JSON antes de enviarlos a Elasticsearch. Los errores de nginx y los logs de infraestructura que no son JSON siguen siendo consultables en el campo `log`.

## Alertas de Grafana

Las reglas se provisionan desde `configmaps/grafana-alerts.yaml`, en dos grupos.

**Infraestructura PharmaGo.** Se evalúan cada 30 segundos y pasan a estado de alerta tras 2 minutos:

| Regla | Condición |
|---|---|
| CPU de nodo alta | Uso de CPU > 85% |
| Memoria de nodo alta | Uso de memoria > 90% |
| Espacio de disco bajo | Disponible en el filesystem principal (`/` o `/data`) < 15% |
| Errores de red de nodo | Tasa de errores de recepción y transmisión > 0 |
| CPU de pod alta | Consumo de un pod > 0.4 núcleos |

**Servicio PharmaGo.** Miden lo que reciben los usuarios a través del gateway (`job="PharmaGo.ApiGateway"`), sin contar `/health`, que consultan las probes de Kubernetes. Se evalúan cada 10 segundos:

| Regla | Condición |
|---|---|
| Errores 5xx en el gateway | Más del 5% de las respuestas del último minuto son 5xx, con al menos 3 errores, durante 30 segundos. Severidad `critical`. |
| Pico de requests en el gateway | Más de 2 requests/s en promedio en el último minuto. Pasa a alerta en la primera evaluación, porque un pico dura poco. |

Cuando un microservicio se cae o se queda sin réplicas, el gateway responde 502 y, con el circuito abierto, 503: la regla de 5xx detecta esa caída. El mínimo de 3 errores evita que un error aislado con poco tráfico la dispare. La regla de pico usa `pharmago_http_request_duration_milliseconds_count`, que no separa por código de estado, así que cuenta también las requests que el rate limit rechaza con 429 (100 por minuto por IP).

Los microservicios exportan sus métricas cada 15 segundos (`OTEL_METRIC_EXPORT_INTERVAL` en sus Deployments; el SDK usa 60 por defecto). En una prueba con el gateway real, la regla de 5xx pasó a alerta unos 70 segundos después de apagar pharmacy o de cortar la red hacia él, y se resolvió alrededor de un minuto después de restaurarlo; la de pico se disparó 21 segundos después de una ráfaga de 200 requests. Las dos quedan dentro de la duración máxima de un escenario de `run-chaos.py` (120 segundos).

El corte de red (escenario `network`) se detecta porque el gateway renueva cada 15 segundos sus conexiones con los servicios; ver [README-k8s.md, sección "Resiliencia ante fallas"](README-k8s.md#resiliencia-ante-fallas). Para que aparezca en la regla de 5xx tiene que haber tráfico hacia pharmacy a través del gateway, porque la sonda de ese escenario llama a pharmacy directamente.

La regla de pico no detecta una ráfaga contra un endpoint que el pod del gateway no había atendido desde que arrancó: la serie aparece en Prometheus ya con todo el conteo, y Prometheus necesita dos muestras para calcular una tasa. Antes del escenario `requests` tiene que haber habido tráfico a `/api/drug`.

Las alertas aparecen en **Grafana → Alerting → Alert rules → PharmaGo**. La instalación de Grafana tiene su contacto predeterminado; para enviar notificaciones fuera de Grafana se debe configurar un contacto con el destino del entorno (correo, webhook, etc.).

## Actualización de un clúster existente

Desde `Implementacion K8S/Codigo/k8s`:

```bash
./build-telemetry-images.sh
./deploy-telemetry.sh
```

`build-telemetry-images.sh` compila y carga las imágenes actuales de los tres microservicios y la UI en todos los nodos Minikube. Es necesario ejecutarlo antes del rollout porque los Deployments usan etiquetas `:latest` con `imagePullPolicy: Never`. El script de despliegue aplica ConfigMaps, RBAC, Jaeger y los Deployments; reinicia los componentes que montan ConfigMaps con `subPath` para que lean los cambios. Los tres microservicios conservan dos réplicas y su estrategia RollingUpdate.

En Windows se puede usar `build-images.ps1` y luego `./deploy-telemetry.sh` desde Git Bash.

Para un clúster nuevo, `apply-k8s.sh`/`apply-k8s.ps1` también instala estos recursos después de `build-images.sh` y la asignación de nodos. El script `place-services.sh` sigue siendo necesario únicamente para fijar los microservicios en los nodos de referencia después del primer despliegue.

## Verificación

```bash
kubectl get pods -n pharmago -o wide
kubectl get deployments -n pharmago
kubectl logs -n pharmago -l app=otlp-collector --tail=100
kubectl logs -n pharmago -l app=pharmago-api-gateway --tail=1
./port-forward.sh
```

En Prometheus (`http://127.0.0.1:9090`), comprobar `node_cpu_seconds_total`, `container_cpu_usage_seconds_total` y `pharmago_http_requests_total`. El dashboard **PharmaGo - Infra** muestra CPU, memoria, storage y red por nodo y por pod. En Grafana (`http://127.0.0.1:3000`), comprobar los siete alert rules y la fuente de datos Jaeger. Generar tráfico con `curl http://127.0.0.1:5000/health` y buscar el servicio `PharmaGo.ApiGateway` en Jaeger (`http://127.0.0.1:16686`). En Kibana (`http://127.0.0.1:5601`), crear el data view `pharmago-logs-*` y filtrar por `kubernetes.labels.app: pharmago-api-gateway`; los eventos JSON nuevos deben tener `Message`, `LogLevel` y `Category`.

Jaeger all-in-one usa almacenamiento en memoria: sus trazas se pierden al reiniciar su pod. Los cinco nodos Minikube de este laboratorio son contenedores sobre una única máquina física; node-exporter mide cada nodo Kubernetes, no separa cinco hosts físicos independientes.
