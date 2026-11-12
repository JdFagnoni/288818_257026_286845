# Experimentos de caos de PharmaGo

`run-chaos.py` cubre los seis fallos pedidos por el obligatorio. Los escenarios se ejecutan de a uno; `--scenario all` recorre los seis en orden. Reemplaza los cuatro scripts originales que escribían archivos o dejaban procesos dentro de los contenedores de la aplicación sin una limpieza garantizada.

## Requisitos previos

- Python 3 y `kubectl` en `PATH`.
- Contexto de Kubernetes apuntando al cluster de prueba `pharmago`, con el namespace `pharmago`, tres microservicios backend disponibles y exactamente un nodo con `node-type=ops`.
- Imagen `busybox:1.36` disponible para el cluster. El primer experimento puede tener que descargarla.

El ejecutor imprime el contexto antes de empezar y aborta si gateway o pharmacy no tienen todas sus réplicas disponibles. Los pods de carga de CPU, memoria y storage se ubican en **el mismo nodo Kubernetes del gateway**, sin cambiar la ubicación de los microservicios. Las sondas HTTP se ubican en el nodo `ops` y llaman al gateway por su Service interno; no hace falta `port-forward`.

## Ejecución

Desde esta carpeta:

```bash
set -o pipefail
python3 run-chaos.py --scenario all --duration 20 --requests 200 \
  2>&1 | tee ../../../docs/evidencia/07-scripts-caos/01-ejecucion-caos.txt
```

Para ejecutar uno solo, usar `--scenario requests`, `cpu`, `memory`, `storage`, `network` o `component`. También se pueden ajustar `--memory-mib` (96 por defecto) y `--storage-mib` (128 por defecto). `--duration` vale para cada escenario; `all` dura varios minutos por el arranque de pods y la recuperación de pharmacy. Para observar los pods de carga en Prometheus y Grafana, ejecutar `cpu`, `memory` o `storage` por separado con `--duration 90`: el Collector recoge algunas métricas de Kubernetes cada 60 s y un pod de 20 s puede no aparecer en esas series.

| Escenario | Inyección | Evidencia esperada |
|---|---|---|
| `requests` | Hasta 200 GET a `/api/drug` desde un pod temporal. | Códigos HTTP en la salida; tráfico y posibles 429/5xx en Grafana. |
| `cpu` | Dos bucles intensivos dentro de un pod temporal, con límite de 1 CPU. | CPU de ese pod y de su nodo en Grafana. |
| `memory` | Archivo de 96 MiB en un `emptyDir` de memoria (`tmpfs`), retenido durante `--duration`. | Memoria del pod y del nodo; no usa `/tmp` como sustituto. |
| `storage` | Archivo de 128 MiB en un `emptyDir` de disco, retenido durante `--duration`. | Uso de almacenamiento efímero del pod y espacio del nodo. |
| `network` | Cambia temporalmente el `targetPort` del Service de pharmacy a un puerto no atendido. Los pods siguen `Ready`; una sonda abre conexiones nuevas al Service para comprobar el corte. | Respuestas fallidas en la sonda durante el corte y HTTP 200 tras restaurarlo. |
| `component` | Escala `pharmago-pharmacy-service` a cero réplicas y luego restaura la cantidad original. | Respuestas fallidas durante la desconexión, regreso a `Ready` y HTTP 200 tras recuperar. |

El script agrega marcas de tiempo UTC y un resumen de las sondas. Los escenarios de red y componente verifican que la ruta funciona antes de cortarla, que aparecen respuestas fallidas durante la inyección y que vuelve a responder después. El corte de red prueba **conexiones nuevas** al Service. El gateway renueva sus conexiones con pharmacy cada 15 segundos, así que, si hay tráfico hacia pharmacy a través del gateway, en ese plazo también empieza a fallar y abre el circuito. Se puede cruzar la hora con el dashboard `PharmaGo - Overview`, `PharmaGo - Infra`, los logs JSON de Kibana y las trazas del gateway.

## Restauración

Los pods temporales se eliminan al terminar cada escenario. El corte de red restaura el `targetPort` original en un bloque `finally`; la desconexión restaura el número original de réplicas y espera el rollout. Esto también se ejecuta ante Ctrl+C o SIGTERM. Si la computadora o el proceso terminan abruptamente sin permitir la limpieza, revisar y restaurar manualmente:

```bash
kubectl get svc pharmago-pharmacy-service -n pharmago -o jsonpath='{.spec.ports[0].targetPort}'
kubectl patch svc pharmago-pharmacy-service -n pharmago --type=json \
  -p='[{"op":"replace","path":"/spec/ports/0/targetPort","value":80}]'
kubectl scale deployment/pharmago-pharmacy-service -n pharmago --replicas=2
kubectl rollout status deployment/pharmago-pharmacy-service -n pharmago --timeout=180s
kubectl delete pods -n pharmago -l app.kubernetes.io/name=pharmago-chaos --ignore-not-found
```

Los valores `80` y `2` son los del despliegue de referencia; si el cluster usaba otros, restaurar los valores que el script imprimió al iniciar cada escenario. La prueba de circuit breaker independiente sigue en `../k8s/test-circuit-breaker.sh` para el ítem de alta disponibilidad.
