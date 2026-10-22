# Evidencia del obligatorio

Capturas y salidas de terminal que respaldan cada ítem de la rúbrica. Cada carpeta corresponde a un ítem y a su issue en el [tablero](https://github.com/users/JdFagnoni/projects/1).

| Carpeta | Contenido | Issue |
|---|---|---|
| `00-linea-base/` | Comportamiento normal del sistema, con tráfico liviano, antes de los cambios de telemetría, resiliencia y caos (ver abajo qué versión es) | [#12](https://github.com/JdFagnoni/288818_257026_286845/issues/12) |
| `01-plataforma/` | Ítem 1. Implementación de plataforma | [#3](https://github.com/JdFagnoni/288818_257026_286845/issues/3) |
| `02-alta-disponibilidad/` | Ítem 2. Alta disponibilidad | [#4](https://github.com/JdFagnoni/288818_257026_286845/issues/4) |
| `03-despliegues-seguros/` | Ítem 3. Despliegues seguros | [#5](https://github.com/JdFagnoni/288818_257026_286845/issues/5) |
| `04-telemetria/` | Ítem 4. Telemetría | [#6](https://github.com/JdFagnoni/288818_257026_286845/issues/6) |
| `05-deteccion-anomalias/` | Ítem 5. Detección de anomalías | [#7](https://github.com/JdFagnoni/288818_257026_286845/issues/7) |
| `06-plan-contencion/` | Ítem 6. Plan de contención de incidentes | [#8](https://github.com/JdFagnoni/288818_257026_286845/issues/8) |
| `07-scripts-caos/` | Ítem 7. Scripts de caos | [#9](https://github.com/JdFagnoni/288818_257026_286845/issues/9) |
| `08-defensa/` | Ítem 8. Defensa (war-room) | [#10](https://github.com/JdFagnoni/288818_257026_286845/issues/10) |

## Convenciones

- **Nombre:** `NN-descripcion.ext`. `NN` es el orden dentro de la carpeta (`01`, `02`, …) y la descripción va en minúsculas con guiones. Ejemplo: `02-grafana-overview.png`.
- **Formato:** capturas de pantalla en PNG; salidas de terminal en `.txt`, copiadas tal cual y con el comando al principio.
- **Índice:** cada archivo nuevo se agrega a la tabla de su carpeta, más abajo, con qué muestra y la fecha.
- **Sin datos sensibles:** nada de tokens, contraseñas personales ni datos de terceros.

## Índice

### 00 · Línea base

Versión: `main` en `6b996db`. Incluye los ítems 1 (plataforma multinodo) y 3 (2 réplicas por microservicio y rolling update sin caída), pero todavía no los cambios de telemetría, circuit breaker, caos ni detección de anomalías. Sirve de referencia del estado normal para esos ítems; no sirve como "antes" de los ítems 1 y 3.

Tráfico durante las capturas: un pod dentro del cluster hace una request cada 4 s al gateway, rotando entre `/api/drug`, `/api/pharmacy` y `/test-health`.

La topología del cluster en esta versión (nodos y pods) está en [01-plataforma/01-nodos-y-pods.png](01-plataforma/01-nodos-y-pods.png).

| Archivo | Qué muestra | Fecha |
|---|---|---|
| [01-grafana-overview-superior.png](00-linea-base/01-grafana-overview-superior.png) | Dashboard *PharmaGo - Overview*: peticiones por segundo, latencia promedio, tasa de error (0 %) y throughput total. `/health` tiene la mayor tasa porque incluye las probes de Kubernetes. | 2026-10-21 |
| [02-grafana-overview-inferior.png](00-linea-base/02-grafana-overview-inferior.png) | Resto del Overview: sin errores 429 ni errores en `/api/drug`. *Distribución de Status Codes* muestra "No data" por un error en la consulta del dashboard (divide series con y sin la etiqueta `status_code`); se corrige en el ítem 4. | 2026-10-21 |
| [03-grafana-infra-pods.png](00-linea-base/03-grafana-infra-pods.png) | Dashboard *PharmaGo - Infra*: disco disponible, y CPU y memoria por pod. Los paneles de nodo muestran una sola serie (`node-exporter:9100`) porque node-exporter se scrapea a través de un Service y mezcla los 4 nodos; se corrige en el ítem 4. | 2026-10-21 |
| [04-kibana-logs-backend.png](00-linea-base/04-kibana-logs-backend.png) | Logs del backend en Kibana (últimos 15 min). Todos son del gateway y están en texto plano con códigos de color de consola, no en JSON; users y pharmacy no registran logs por request. Ambas cosas se resuelven en el ítem 4. | 2026-10-21 |
| [05-kibana-logs-pharmacy-json.png](00-linea-base/05-kibana-logs-pharmacy-json.png) | Logs de pharmacy (últimas 24 h): son JSON estructurado, por eso Kibana separa `LogLevel` y `Message` en campos. Solo aparecen los del arranque. | 2026-10-21 |
| [06-metricas-linea-base.txt](00-linea-base/06-metricas-linea-base.txt) | Valores de referencia de los últimos 15 minutos sacados de Prometheus: tráfico, latencia promedio y p95 por endpoint, tasa de error, CPU y memoria de nodo y de cada pod del backend, con las consultas PromQL usadas. Sirve para fijar los umbrales de las alertas. | 2026-10-21 |

### 01 · Plataforma

| Archivo | Qué muestra | Fecha |
|---|---|---|
| [01-nodos-y-pods.png](01-plataforma/01-nodos-y-pods.png) | Los 4 nodos con su rol (`node-type`: 1 `ops` y 3 `backend`) y todos los pods del namespace `pharmago`, con el nodo donde corre cada uno. Cada microservicio tiene sus 2 réplicas en un nodo propio. Cluster de 4 nodos (IPs `192.168.58.x`). | 2026-10-21 |
| [02-diagrama-despliegue.png](01-plataforma/02-diagrama-despliegue.png) | Diagrama de despliegue generado desde [deployment-diagram.md](../../Implementacion%20K8S/Codigo/k8s/deployment-diagram.md): servicios, pods por nodo, DaemonSets y flujos de telemetría y logs. Muestra la ubicación observada en otro cluster del equipo, de 5 nodos (IPs `192.168.49.x`); por eso los nodos no coinciden con la captura anterior. En los dos se cumple la regla: cada microservicio en un nodo distinto. | 2026-10-21 |

### 03 · Despliegues seguros

Técnica: rolling update (`maxSurge: 1`, `maxUnavailable: 0`, `minReadySeconds: 5`), `preStop` de 5 s, probes `httpGet /health` y warm-up de la base en pharmacy. La explicación está en [README-k8s.md, sección "Despliegue sin caída"](../../Implementacion%20K8S/Codigo/k8s/README-k8s.md#despliegue-sin-caída). Versión: `main` en `6b996db`, con el mismo tráfico de fondo que la línea base.

| Archivo | Qué muestra | Fecha |
|---|---|---|
| [01-test-zero-downtime.txt](03-despliegues-seguros/01-test-zero-downtime.txt) | Salida de `test-zero-downtime.sh` reemplazando gateway, users y pharmacy, uno tras otro: 164 requests, 0 fallidos (100 % de disponibilidad). | 2026-10-21 |
| [02-pods-durante-rollout.txt](03-despliegues-seguros/02-pods-durante-rollout.txt) | Eventos de los pods del backend con la hora de cada cambio. En cada servicio, el pod nuevo queda `Ready` antes de que empiece a terminar uno viejo, siempre en el nodo de ese servicio. | 2026-10-21 |
| [03-grafana-overview-rollout-superior.png](03-despliegues-seguros/03-grafana-overview-rollout-superior.png) | Overview durante los rollouts (18:45 a 18:47): el tráfico sigue sin cortes y la tasa de error se mantiene en 0 %. La latencia de `/api/drug` sube hasta ~35 ms mientras se reemplaza pharmacy, por las primeras consultas en los pods nuevos, sin errores. | 2026-10-21 |
| [04-grafana-overview-rollout-inferior.png](03-despliegues-seguros/04-grafana-overview-rollout-inferior.png) | Resto del Overview: ni errores 429 ni errores en `/api/drug` durante los rollouts. | 2026-10-21 |
| [05-grafana-infra-rollout-nodo.png](03-despliegues-seguros/05-grafana-infra-rollout-nodo.png) | Infra: CPU y memoria de nodo estables antes y durante los rollouts (serie única; ver la limitación en la línea base). | 2026-10-21 |
| [06-grafana-infra-rollout-pods.png](03-despliegues-seguros/06-grafana-infra-rollout-pods.png) | Infra: disco, CPU y memoria por pod. Reemplazar los pods no genera picos de recursos. | 2026-10-21 |
