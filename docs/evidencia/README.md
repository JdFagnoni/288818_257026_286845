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

El requisito de que los microservicios no compartan "nodos físicos" se cumple con nodos de Kubernetes distintos dentro de la misma máquina (cluster Minikube multinodo). El docente confirmó esta interpretación ante la consulta del equipo.

| Archivo | Qué muestra | Fecha |
|---|---|---|
| [01-nodos-y-pods.png](01-plataforma/01-nodos-y-pods.png) | Los 4 nodos con su rol (`node-type`: 1 `ops` y 3 `backend`) y todos los pods del namespace `pharmago`, con el nodo donde corre cada uno. Cada microservicio tiene sus 2 réplicas en un nodo propio. | 2026-10-21 |
