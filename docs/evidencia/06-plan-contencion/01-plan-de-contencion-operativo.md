# Plan de contención y mitigación de incidentes operacionales — PharmaGo

AIOps · Universidad ORT Uruguay · Juan Diego Fagnoni (288818), Federico Galusso (257026), Juan Pedro Michelini (286845)

Ítem 6 de la rúbrica. Se usa **en vivo** en el war-room. Sigue el ciclo **Prepare → Detect → Respond → Recover → Learn**.

- Comandos: se corren en una terminal Bash o zsh, desde `Implementacion K8S/Codigo/k8s`, sobre el namespace `pharmago`.
- Horas: Grafana y el registro van en hora local (UTC-3). `run-chaos.py` imprime **UTC** (la hora local más 3 h).
- `Qn` = consulta PromQL y `Kn` = comando kubectl, los dos de la [sección 13](#13-referencia-rápida-consultas-y-comandos).

## Introducción y objetivos

Este plan define cómo **detectar, clasificar, contener y recuperar** PharmaGo ante incidentes que comprometan su disponibilidad o su rendimiento. Busca minimizar el impacto sobre los usuarios y evitar que las fallas se propaguen.

Se apoya en los mecanismos que ya tiene la plataforma:

- recuperación automática de Kubernetes (probes y reinicio),
- réplicas de los microservicios,
- en el gateway: circuit breaker, reintentos con backoff, timeout y rate limiting,
- la pila de observabilidad.

También es la guía para responder durante las pruebas de ingeniería del caos y la defensa.

---

## Resumen: escenario → respuesta

| Escenario | Alerta que salta | Mecanismo automático | Mitigación manual | SEV típica |
|---|---|---|---|---|
| `requests` | `Pico de requests en el gateway` (~21 s) | Rate limiting del gateway → `429` | Borrar el pod que inunda; escalar el gateway si hay 5xx | SEV3 |
| `cpu` | `CPU de pod alta` / `CPU de nodo alta` (solo si la carga dura más de ~4 min) | Límite de CPU del pod; liveness probe | Borrar el pod de carga o el pod afectado | SEV3 |
| `memory` | `Memoria de nodo alta` (solo con más de 90 % de uso) | Límite de memoria → OOMKilled y reinicio | Borrar el pod de carga o el pod afectado | SEV3 |
| `storage` | `Espacio de disco bajo` (solo con menos de 15 % libre) | `sizeLimit` / límite de `ephemeral-storage` → desalojo | Borrar el pod de carga o el archivo | SEV3 |
| `network` | `Errores 5xx en el gateway` (~70 s, con tráfico hacia pharmacy; `Errores de red de nodo` **no** salta) | Renovación de conexiones cada 15 s, reintentos y circuit breaker en el gateway | Restaurar `targetPort` 80 del Service | SEV1–SEV2 |
| `component` | `Errores 5xx en el gateway` (~70 s) | Circuit breaker → `503` inmediato; 2 réplicas y probes si muere un pod | `kubectl scale ... --replicas=2` | SEV1 (un solo pod caído: SEV4) |

> **Ojo:** hay 7 alertas en dos grupos.
>
> - Las 2 del grupo **Servicio PharmaGo** (5xx y pico de requests) se evalúan cada 10 s y saltan dentro de la duración de un escenario: el pico a los ~21 s y los 5xx a los ~70 s.
> - Las 5 del grupo **Infraestructura PharmaGo** usan `for: 2m`, y las de CPU además `rate(...[5m])`. Con los valores por defecto del script (`--duration 20`, `96` MiB, `128` MiB) **no llegan a dispararse**. En `cpu`, `memory` y `storage`, la detección real se hace con los paneles de *PharmaGo - Infra* y con K3.
>
> Las alertas de servicio necesitan **tráfico de fondo** (sección 12.2): sin requests a pharmacy por el gateway no hay 5xx que contar, y el pico no se detecta si el gateway no había atendido antes `/api/drug`.

---

## 1. Alcance y servicios críticos

**Cubre:** incidentes operacionales del cluster Minikube `pharmago` (5 nodos, namespace `pharmago`). Son seis escenarios principales:

1. sobrecarga de solicitudes HTTP,
2. sobrecarga de CPU,
3. sobrecarga de memoria,
4. sobrecarga de almacenamiento,
5. interrupción o degradación de red,
6. desconexión de componentes internos.

Corresponden a `requests`, `cpu`, `memory`, `storage`, `network` y `component` de `run-chaos.py`, y a sus variantes.

**No cubre:** incidentes de seguridad ni la recuperación de datos perdidos de la base.

| Componente | Deployment / Service | Nodo | Réplicas | Depende de | Si falla… |
|---|---|---|---|---|---|
| Frontend (Angular) | `pharmago-ui` | `pharmago-m03` | 1 | gateway | No carga la interfaz web |
| API Gateway (YARP) | `pharmago-api-gateway` | `pharmago-m03` | 2 | users, pharmacy | **Todo** cae |
| Users Service | `pharmago-users-service` | `pharmago` | 2 | base | Cae el **login** y la gestión de usuarios (`/api/login`, `/api/users`, `/api/roles`, `/api/invitations`) |
| Pharmacy Service | `pharmago-pharmacy-service` | `pharmago-m04` | 2 | base | Caen las **compras**, las farmacias y los medicamentos (`/api/purchases`, `/api/drug`, `/api/pharmacy`, `/api/stockrequest`, `/api/unitmeasure`) |
| SQL Server | `pharmago-db` | `pharmago-m02` (ops) | 1 | PV `hostPath` | users y pharmacy responden `503 Database unavailable` |
| Observabilidad | `prometheus`, `grafana`, `otlp-collector`, `jaeger`, `elasticsearch`, `kibana`; DaemonSets `node-exporter` y `fluent-bit` | `pharmago-m02` (DaemonSets en los 5 nodos) | 1 | — | Se pierde visibilidad, no servicio |
| Kubernetes | Control plane en `pharmago` | — | — | — | Administra la ejecución, disponibilidad y recuperación de los contenedores |

- **Cadena crítica:** UI → gateway → users / pharmacy → base.
- **SQL Server y su volumen `hostPath` son puntos únicos de falla de datos; la UI también tiene una sola réplica.**
- Las 2 réplicas de cada microservicio **comparten nodo**. Toleran la caída de un pod, pero no la de su nodo.

---

## 2. Roles y responsabilidades

Durante un incidente se arma el **war-room** con tres roles. **Los roles se asignan al comenzar cada incidente.** Si hace falta, un integrante puede asumir más de uno.

| Rol (abreviatura) | Responsabilidades | No hace |
|---|---|---|
| **Incident Commander (IC)** | Coordina la respuesta, clasifica el incidente (SEV), establece prioridades, autoriza las acciones y determina el cierre. Habla con los docentes. | No ejecuta comandos. |
| **Responsable técnico (RT)** | Diagnostica las fallas, inspecciona los componentes afectados (K\*, Q\*) y ejecuta la contención y la recuperación **cuando el IC las autoriza**. Anuncia en voz alta cada comando que cambia algo. | No aplica solo una mitigación con riesgo. |
| **Responsable de observabilidad y comunicaciones (RO)** | Supervisa las métricas y alertas (Grafana, Kibana, Jaeger), registra los acontecimientos con la hora ([sección 9](#9-plantilla-de-registro-de-incidente)) y mantiene informado al equipo con los mensajes de estado. | No corre comandos que modifiquen el cluster. |

Asignación por defecto: **IC Federico Galusso** · **RT Juan Diego Fagnoni** · **RO Juan Pedro Michelini**. Al abrir cada incidente el IC la confirma o la cambia (por ejemplo, si alguien está ocupado con otro incidente).

---

## 3. Clasificación de incidentes

### Cuándo se declara un incidente

Alcanza con **una** de estas condiciones:

1. Una alerta de *Grafana → Alerting → Alert rules → PharmaGo* en estado **Firing**.
2. La tasa de 5xx del gateway (Q1) está **por encima del 1 % durante 1 min**.
3. Un deployment backend tiene menos réplicas disponibles que las deseadas (K2 no muestra `2/2`).
4. K3 muestra un pod con `app.kubernetes.io/name=pharmago-chaos`, o aparece un pod que no está en los manifiestos.
5. Una prueba desde dentro del cluster (K8) no devuelve `200`.

### Niveles de severidad

| | **SEV1 — Crítica** | **SEV2 — Alta** | **SEV3 — Media** | **SEV4 — Baja** |
|---|---|---|---|---|
| Descripción | Indisponibilidad general, de login o de compras, o riesgo para la integridad de los datos | Interrupción de una funcionalidad secundaria o fallas reiteradas de comunicación | Degradación del rendimiento sin interrupción | Evento transitorio sin impacto significativo |
| Tasa de 5xx del gateway (Q1) | > 25 % | 1–25 % | < 1 % | 0 % |
| Funcionalidad | Login **o** compras no disponibles | Falla una ruta secundaria; login y compras funcionan | Ninguna ruta falla | Ninguna ruta falla |
| Servicios | Gateway o base caídos; users o pharmacy con 0 réplicas disponibles; 2 o más servicios afectados | Un servicio con errores intermitentes | Solo recursos (CPU, memoria, disco) o `429` | Un pod que se reinicia y se recupera solo |
| Latencia (panel *Latencia Promedio por Endpoint*) | — | > 1000 ms (umbral rojo del panel) | Por encima de la línea base (~5 ms) pero < 1000 ms | Normal |
| Ejemplos | Pharmacy escalado a 0, SQL Server caído, corte de red que tira las compras | Corte de red solo contra el Service directo; 5xx intermitentes | Escenarios `cpu`, `memory`, `storage` y `requests` | Pod borrado con la otra réplica atendiendo (MTTR ~6 s) |
| Reconocer (ack) | ≤ 1 min | ≤ 2 min | ≤ 5 min | Al registrarlo |
| Empezar a mitigar | Inmediato (≤ 3 min) | ≤ 10 min | Cuando lo indique el IC | Normalmente no hace falta |
| Actualización de estado | Cada 2 min | Cada 5 min | Al abrir y al cerrar | Solo en el registro |

- Ante la duda entre dos niveles, se elige el **más alto**. Se baja cuando se confirma el impacto real.
- El panel *Tasa de Error por Endpoint (%)* cuenta 4xx y 5xx: un pico de `429` aparece como "error". **Para decidir la SEV se usa Q1, que cuenta solo 5xx.**

### Escalamiento

El IC **sube la severidad** (y lo comunica) cuando:

- el impacto crece o se suma otro componente afectado,
- la recuperación automática no alcanza (por ejemplo, pasan 2 min sin que las réplicas vuelvan a `READY`),
- hay riesgo para la integridad de los datos de la base,
- las medidas de contención no reducen Q1.

---

## 4. Detección y monitoreo

| Herramienta | Qué aporta | Dónde |
|---|---|---|
| **Prometheus + Grafana** | Métricas, alertas y los dashboards *PharmaGo - Overview* (tráfico, latencia, errores, `429`) y *PharmaGo - Infra* (CPU, memoria, disco y red por nodo y por pod) | http://127.0.0.1:3000 · http://127.0.0.1:9090 |
| **Elasticsearch + Kibana** | Logs JSON de los tres microservicios: excepciones, reintentos, aperturas del circuit breaker, `Database unavailable` | http://127.0.0.1:5601, data view `pharmago-logs-*` |
| **OpenTelemetry + Jaeger** | Trazas distribuidas: en qué tramo de la llamada aparece el error o la demora | http://127.0.0.1:16686 |
| **Kubernetes** | Pods no disponibles, reinicios, fallas de probes, OOMKilled, desalojos, nodos `NotReady` | K1–K4 |

### Alertas actuales

Provisionadas en `k8s/configmaps/grafana-alerts.yaml`, en la carpeta **PharmaGo**. Son 7 reglas en dos grupos:

- **Infraestructura PharmaGo:** se evalúan cada 30 s y pasan a **Firing** cuando la condición se mantiene durante `for: 2m`.
- **Servicio PharmaGo:** se evalúan cada 10 s y miden lo que recibe el usuario a través del gateway (`job="PharmaGo.ApiGateway"`, sin contar `/health`). Los microservicios exportan métricas cada 15 s (`OTEL_METRIC_EXPORT_INTERVAL`).

| Alerta | Condición exacta | Detecta en el caos… |
|---|---|---|
| `CPU de nodo alta` | CPU del nodo > 85 % (`rate[5m]`) | Solo con carga sostenida que sature el nodo |
| `Memoria de nodo alta` | Memoria usada del nodo > 90 % | Improbable con 96 MiB |
| `Espacio de disco bajo` | Disponible en `/` o `/data` < 15 % | Solo si el disco ya estaba casi lleno |
| `Errores de red de nodo` | Errores de recepción o transmisión de la placa de red > 0 | **No** detecta el escenario `network`: ahí no hay errores de placa, sino un Service apuntando a un puerto equivocado |
| `CPU de pod alta` | Un pod usa > 0.4 núcleos (`rate[5m]`) | Recién ~4 min después de empezar una carga de 1 núcleo |
| `Errores 5xx en el gateway` (`critical`) | Más del 5 % de las respuestas del último minuto son 5xx, con al menos 3 errores, durante `for: 30s` | `component` y `network`: salta ~70 s después de la caída y se resuelve ~1 min después de restaurar. En `network` necesita tráfico hacia pharmacy por el gateway. |
| `Pico de requests en el gateway` (`warning`) | Más de 2 req/s promedio en el último minuto, `for: 0s`. Incluye los `429` | `requests`: salta ~21 s después de una ráfaga de 200. No la detecta si el pod del gateway no había atendido antes ese endpoint. |

Por eso la detección **no depende solo de las alertas**. El RO mira los paneles y el RT corre K2 y K3 apenas el IC anuncia que empezó el caos.

---

## 5. Escalamiento y comunicación

- **Canal principal:** reunión y chat de **Microsoft Teams**. Si Teams no está disponible, el IC pasa la coordinación a una llamada telefónica grupal. Las decisiones se comunican en el canal activo y quedan en el registro; no se coordinan acciones simultáneas por canales paralelos.
- **Quién habla con los docentes:** el IC.
- **Frecuencia de actualización:** según la SEV ([sección 3](#3-clasificación-de-incidentes)).
- **Registro centralizado:** la plantilla de la [sección 9](#9-plantilla-de-registro-de-incidente). Campos: identificador, severidad, fecha y hora, componente afectado, impacto, acciones realizadas, estado y responsable.
- **Estados:** `INVESTIGANDO` → `CONTENIDO` → `RECUPERÁNDOSE` → `RESUELTO`.
- **Formato del mensaje de estado** (lo escribe el RO y lo lee el IC):

```
[INC-#] [SEV#] [INVESTIGANDO | CONTENIDO | RECUPERÁNDOSE | RESUELTO] hh:mm
Impacto: <qué no funciona para el usuario, ej. "compras fallan con 503">
Componente: <deployments afectados>
Causa probable: <hipótesis actual o "en diagnóstico">
Acción en curso: <qué se está haciendo> — Responsable: <RT/RO/IC>
Próxima actualización: hh:mm
```

---

## 6. Procedimiento general de respuesta

| # | Etapa | Quién | Cómo |
|---|---|---|---|
| 1 | **Detección** | RO + RT | Llega una alerta, un panel, un log o una falla de la app. Confirmar en Grafana y correr K1, K2 y K3: ¿es real? Anotar **T_detección** y el síntoma principal. |
| 2 | **Clasificación** | IC | Asignar los roles. Fijar la severidad según la [sección 3](#3-clasificación-de-incidentes) y anotar **T_reconocimiento**. Mandar el primer mensaje de estado (`INVESTIGANDO`). |
| 3 | **Diagnóstico** | RT | Seguir el runbook de la [sección 7](#7-runbooks-por-escenario) con métricas, logs, trazas y estado de Kubernetes. Confirmar el origen **y descartar** las otras causas (K6 descarta cambios respecto de los manifiestos). |
| 4 | **Contención** | RT (autoriza el IC) | Primero se deja actuar a lo automático (columna "Qué hace el sistema solo"). Si no alcanza, mitigación manual: primero las 🟢; las 🟠 solo con OK del IC. Estado `CONTENIDO`. |
| 5 | **Recuperación** | RT | Restablecer los componentes: pods `READY`, réplicas esperadas, comunicación funcionando. Estado `RECUPERÁNDOSE`. |
| 6 | **Verificación y cierre** | RO + IC | Criterios de la [sección 8](#8-verificación-de-la-recuperación). Revertir las medidas temporales. Anotar **T_restauración**. El IC autoriza el cierre (`RESUELTO`). Calcular los indicadores y borrar los pods auxiliares. |
| 7 | **Postmortem** | Los 3 | [Sección 10](#10-análisis-posterior-al-incidente-postmortem), en las 24 h siguientes. |

Riesgo de las mitigaciones: 🟢 seguro (reversible, sin corte) · 🟠 con riesgo (puede cortar servicio o no ser reversible) · 🔴 no hacer en vivo.

---

## 7. Runbooks por escenario

Hay dos formas de inyectar caos:

- **Variante A — `run-chaos.py`** (el script del repo):
  - Crea pods temporales `pharmago-chaos-<escenario>-xxxx`, con la etiqueta `app.kubernetes.io/name=pharmago-chaos`, en el nodo del gateway (`pharmago-m03`). Las sondas van al nodo `ops`.
  - **No toca los pods de la aplicación**, salvo en `network` y `component`.
- **Variante B — scripts originales del curso** (`cpu-spike.sh`, `ram-spike.sh`, `volume-spike.sh`, `load-requests.sh`, eliminados del repo en el commit `bc8c624`):
  - Hacen `kubectl exec` dentro del **primer pod** de un deployment (por defecto `pharmago-api-gateway`).
  - Dejan procesos o archivos adentro (`/tmp/chaos_fill`, `/dev/shm/fill`).

**El primer comando siempre es K3.** Si aparece un pod `pharmago-chaos-*`, es la variante A y su nombre indica el escenario.

### 7.1 Volumen excesivo de solicitudes HTTP (`requests`)

**Inyección:**
- A: hasta 200 `GET /api/drug` seguidos, desde un pod en `ops`.
- B: 500 `POST /api/login` en paralelo, vía port-forward.

**Síntoma**

- Alerta: `Pico de requests en el gateway` (más de 2 req/s en el último minuto). Salta ~21 s después de la ráfaga y cuenta también los `429`. Requisito: el gateway tiene que haber atendido antes `/api/drug` (el tráfico de fondo lo cubre). Si no, la serie aparece con todo el conteo de golpe y Prometheus no puede calcular la tasa.
- Grafana, *PharmaGo - Overview*:
  - suben *Peticiones por Segundo por Endpoint* y *Throughput Total*,
  - aparecen `429` en *Rate Limit Errors (429) por Endpoint* y en *Tasa de Error - /api/drug (429 y Total)*,
  - puede subir la latencia.
- Kibana: `kubernetes.labels.app : "pharmago-api-gateway"` durante el pico.

**Diagnóstico**

1. K3: buscar `pharmago-chaos-requests-*`. Para la variante B, preguntar si hay un `curl` corriendo contra `127.0.0.1:5000`.
2. Identificar el endpoint más pedido (Q2) y cuántos `429`/s hay (Q4).
3. Confirmar que el rate limiting rechaza el exceso y que los servicios no se degradan: Q1 (5xx) cerca de 0 y Q5 (latencia) por debajo de 1000 ms.
4. Mirar la CPU de los pods `pharmago-api-gateway-*` con Q6. Su límite es 0.5 núcleos: si están ahí, el gateway está saturado.

**Qué hace el sistema solo**

- **Rate limiting por IP**, con respuesta `429`:
  - 100 req/min y 1000 req/h en todas las rutas,
  - **10 req/min en `POST /api/login`**,
  - `/health` y `/metrics` quedan excluidos.
- El contador está en la memoria de **cada réplica** del gateway (`AddInMemoryRateLimiting`). Por eso el límite efectivo por IP es de ~200 req/min.
- Observado (2026-11-03): 200 requests en ~6 s dieron **197 `200` y 3 `429`**, sin 5xx.

**Mitigación manual** (solo si aparecen 5xx o la latencia supera 1000 ms)

| Acción | Comando | Riesgo |
|---|---|---|
| Cortar la fuente (variante A u otro pod) | `kubectl delete pod <pod-origen> -n pharmago` | 🟢 |
| Escalar el gateway | `kubectl scale deployment/pharmago-api-gateway -n pharmago --replicas=3` | 🟠 Las réplicas tienen afinidad con `pharmago-m03`: si no hay memoria, el pod queda `Pending`. Además sube el límite efectivo por IP (~300/min). Volver a 2 al cerrar. |
| Ajustar los límites con un cambio controlado de configuración | `kubectl set env deployment/pharmago-api-gateway -n pharmago IpRateLimiting__GeneralRules__1__Limit=50`. Probado el 2026-11-14 con `=20`: 60 requests dieron 40 `200` y 20 `429`; al revertir, 60 `200` ([evidencia](01-prueba-ajuste-rate-limit.txt)). En modo `IP`, el límite que se aplica está en `IpRateLimiting.GeneralRules`, no en `RateLimiting.MaxRequestsPerMinute`. El índice `1` es la regla `*` por minuto (el `0` es la de login), y los `__` son la sintaxis de ASP.NET Core para pisar `appsettings.json` desde una variable de entorno. | 🟠 Provoca un rolling update. Cada réplica lleva su propio contador, así que el límite conjunto queda en ~2 × `<n>`. Revertir con `kubectl set env deployment/pharmago-api-gateway -n pharmago IpRateLimiting__GeneralRules__1__Limit-`. |

El ajuste con `kubectl set env` es una contención temporal. Para cambiar la regla de forma permanente, editar `Backend/PharmaGo.ApiGateway/appsettings.json`, reconstruir la imagen del gateway y desplegarla con RollingUpdate.

**Validación:**
- Q2 vuelve a la línea base (~0.08 req/s por endpoint de negocio).
- No hay `429` nuevos en el panel.
- Q1 = 0 y la latencia está en su valor normal.
- K3 vacío.

**Severidad típica:** SEV3. Pasa a SEV2 si aparecen 5xx o si usuarios legítimos reciben `429` en el login.

---

### 7.2 Sobrecarga de CPU (`cpu`)

**Inyección:**
- A: pod `pharmago-chaos-cpu-*` con 2 bucles infinitos y límite de 1 CPU, en `pharmago-m03`.
- B: 4 bucles dentro de un pod `pharmago-api-gateway-*` (límite 0.5 CPU) durante 30 s.

**Síntoma**

- Alertas:
  - `CPU de pod alta` (> 0.4 núcleos, `rate[5m]`, `for: 2m`) recién salta **~4 min después de empezar** una carga de 1 núcleo. Con el máximo del script (`--duration 120`) no llega a saltar.
  - `CPU de nodo alta` (> 85 %).
- Grafana, *PharmaGo - Infra*:
  - en *CPU por pod (pharmago)*, el pod de carga cerca de 100 (% de un núcleo),
  - en *CPU %*, sube la serie de `pharmago-m03`.
- Variante B: sube *Latencia Promedio por Endpoint* en Overview.

**Diagnóstico**

1. Identificar el pod o nodo con mayor consumo:
   - K3 → `pharmago-chaos-cpu-*` y su nodo,
   - Q6 (ventana de 1 m, más rápida que el panel),
   - Q9 para el nodo,
   - K7 `kubectl top pods -n pharmago`. Requiere el addon metrics-server de Minikube (chequeo en 12.3); si no está, usar Q6.
2. Determinar el origen con K1, K4 y los logs del pod (K5). En la variante B no hay pod de caos: el consumo lo tiene un pod de la app.
3. Verificar los límites configurados: 0.5 CPU en los pods de la app y 1 CPU en el pod de carga.
4. Descartar impacto: con Q1 = 0 y Q5 < 1000 ms, solo están afectados los recursos (SEV3).

**Qué hace el sistema solo**

- El kernel aplica los límites (1 CPU el pod de carga, 0.5 CPU los de la app), así que ningún pod puede tomar todo el nodo.
- Si `/health` deja de responder, la liveness probe (3 fallos cada 10 s) reinicia el contenedor.
- Variante A: el pod se borra solo al terminar `--duration`.
- Observado: **pico de 0.997 núcleos** en una corrida de 75 s.

**Mitigación manual** (reducir o detener la carga)

| Acción | Comando | Riesgo |
|---|---|---|
| Borrar el pod de carga (A) | `kubectl delete pods -n pharmago -l app.kubernetes.io/name=pharmago-chaos --ignore-not-found` | 🟢 |
| Matar los bucles de un pod de la app (B): reiniciar ese pod | `kubectl delete pod <pod-afectado> -n pharmago` | 🟢 La otra réplica sigue atendiendo. MTTR observado: 5.6–6.8 s. |
| Reemplazar todos los pods del deployment | `kubectl rollout restart deployment/<deployment> -n pharmago` | 🟢 Rolling update probado sin caída (192 requests, 0 fallidos). |

**Validación:**
- El pod de carga ya no aparece en *CPU por pod (pharmago)*.
- Q6 vuelve a < 0.01 núcleos por pod del backend.
- Los pods siguen `READY` y la latencia está en su valor normal.
- K3 vacío y la alerta en **Normal**.

**Severidad típica:** SEV3. Pasa a SEV2 si la latencia supera los 1000 ms.

---

### 7.3 Sobrecarga de memoria RAM (`memory`)

**Inyección:**
- A: pod `pharmago-chaos-memory-*` que llena 96 MiB en `tmpfs` (límite de memoria 120 Mi), en `pharmago-m03`.
- B: `dd` de 200 MB a `/dev/shm/fill` (o a `/tmp/fill`) dentro de un pod `pharmago-api-gateway-*` (límite 384 Mi).

**Síntoma**

- Alerta: `Memoria de nodo alta` (> 90 %, `for: 2m`). Con 96 MiB es improbable que salte.
- Grafana, *PharmaGo - Infra*:
  - el pod de carga aparece en *Memoria por pod (pharmago)* (~96 MiB),
  - sube la serie de `pharmago-m03` en *Memoria usada %*.
- Reinicios de contenedores o eventos `OOMKilled`.

**Diagnóstico**

1. Identificar el componente afectado:
   - K3 → `pharmago-chaos-memory-*`,
   - Q7 para la memoria por pod (línea base: 47–86 MiB por pod del backend).
2. Revisar los eventos y los límites:
   - columna `RESTARTS` de K1,
   - `kubectl describe pod <pod> -n pharmago` → `Last State: Terminated, Reason: OOMKilled`,
   - K4.
3. Q10 para la memoria de `pharmago-m03`.

**Qué hace el sistema solo**

- Si un contenedor supera su límite de memoria, Kubernetes lo mata (OOMKilled) y lo reinicia. El archivo en `/dev/shm` o `/tmp` desaparece con el contenedor, y la otra réplica sigue atendiendo.
- Variante A: el pod se borra al terminar `--duration`.
- Observado: **pico de 96.5 MiB** en el pod de carga.

**Mitigación manual** (detener lo que consume; si los reinicios siguen, investigar la causa)

| Acción | Comando | Riesgo |
|---|---|---|
| Borrar el pod de carga (A) | `kubectl delete pods -n pharmago -l app.kubernetes.io/name=pharmago-chaos --ignore-not-found` | 🟢 |
| Liberar la memoria del pod de la app (B) | `kubectl exec -n pharmago <pod> -- rm -f /dev/shm/fill /tmp/fill` | 🟢 |
| Reiniciar el pod afectado | `kubectl delete pod <pod> -n pharmago` | 🟢 MTTR observado: 5.6–6.8 s |

**Validación:**
- Q7 vuelve a la línea base.
- No quedan pods `pharmago-chaos-*`.
- `RESTARTS` no sigue subiendo y los pods están en `READY`.
- *Memoria usada %* de `pharmago-m03` vuelve a su valor previo.

**Severidad típica:** SEV3. Un OOMKilled aislado con recuperación automática es SEV4. Pasa a SEV2 si los reinicios generan 5xx.

---

### 7.4 Sobrecarga de almacenamiento (`storage`)

**Inyección:**
- A: pod `pharmago-chaos-storage-*` que escribe 128 MiB en un `emptyDir` de disco (`sizeLimit` 144 Mi, límite de `ephemeral-storage` 160 Mi).
- B: `dd` de 300 MB a `/tmp/chaos_fill` dentro de un pod `pharmago-api-gateway-*`. Ese pod **no tiene límite de `ephemeral-storage`**.

**Síntoma**

- Alerta: `Espacio de disco bajo` (< 15 % libre en `/` o `/data`, `for: 2m`). Con 128 MiB solo salta si el disco ya estaba casi lleno.
- Grafana, *PharmaGo - Infra*:
  - el pod aparece en *Storage efímero por pod (pharmago)*,
  - baja la serie de `pharmago-m03` en *Disco disponible %*.
- El Collector toma esta métrica cada 60 s: un pod que dura 20 s puede no aparecer.
- Errores de escritura en los logs.

**Diagnóstico**

1. Identificar el nodo o el volumen afectado:
   - K3 → `pharmago-chaos-storage-*`,
   - Q8 por pod y Q11 por nodo,
   - `minikube ssh -n pharmago-m03 -- df -h /`.
2. **Distinguir el almacenamiento temporal del persistente:**
   - Temporal: `emptyDir` o la capa del contenedor (`/tmp`). Se libera borrando el archivo o el pod.
   - Persistente: los PV `hostPath` en `/mnt/data` de `pharmago-m02` (SQL Server, Prometheus, Elasticsearch, Grafana).
3. Variante B: `kubectl exec -n pharmago <pod> -- du -sh /tmp` y `kubectl exec -n pharmago <pod> -- ls -la /tmp`.

**Qué hace el sistema solo**

- Variante A: si el pod supera su `sizeLimit` o su límite de `ephemeral-storage`, el kubelet lo desaloja (Evicted). Se borra al terminar `--duration`. Observado: **pico de 128.06 MiB**.
- Variante B: **nada**. El archivo queda en el contenedor hasta que el pod se reinicia.

**Mitigación manual** (borrar solo archivos temporales ya identificados)

| Acción | Comando | Riesgo |
|---|---|---|
| Borrar el pod de carga (A) | `kubectl delete pods -n pharmago -l app.kubernetes.io/name=pharmago-chaos --ignore-not-found` | 🟢 |
| Borrar el archivo (B) | `kubectl exec -n pharmago <pod> -- rm -f /tmp/chaos_fill` | 🟢 |
| Reiniciar el pod afectado | `kubectl delete pod <pod> -n pharmago` | 🟢 |
| Almacenamiento persistente lleno | Evaluar ampliar la capacidad o un procedimiento de recuperación, fuera del war-room | 🟠 |
| Limpiar imágenes o volúmenes del nodo | `minikube ssh -n <nodo> -- docker system prune` | 🔴 Las imágenes de la app usan `imagePullPolicy: Never`: si se borran, no hay de dónde volver a bajarlas. |
| Liberar espacio en `pharmago-m02` (ops) | — | 🔴 **Nunca** borrar `/mnt/data`, archivos de SQL Server ni otros PV sin un procedimiento específico y autorizado: no hay réplica de esos datos. |

**Validación:**
- Q8 ya no muestra el pod de carga y el archivo está borrado.
- *Disco disponible %* vuelve a su valor previo.
- K8 contra `/api/drug` devuelve `200` y confirma lectura del catálogo; si hubo errores de escritura, probar también la operación afectada con datos de prueba.
- La alerta está en **Normal** y K3 vacío.

**Severidad típica:** SEV3. Pasa a SEV2 si se desaloja un pod de la app o el nodo baja del 15 % libre. Pasa a SEV1 si está en riesgo el almacenamiento de la base.

---

### 7.5 Interrupción o degradación de red (`network`)

**Inyección (A):** el script cambia el `targetPort` del Service `pharmago-pharmacy-service` de `80` a `1` durante `--duration`.
- Los pods siguen `Ready`, porque las probes van directo a la IP del pod.
- Las conexiones **nuevas** al Service se rechazan.

**Síntoma**

- Alerta: `Errores 5xx en el gateway`, ~70 s después del corte, **solo si hay tráfico hacia pharmacy por el gateway**: la sonda del script llama a pharmacy directamente. `Errores de red de nodo` **no** salta: no hay errores en la placa de red, el puerto está mal.
- Si el gateway abre conexiones nuevas, en Overview suben:
  - *Tasa de Error por Endpoint (%)* en las rutas de pharmacy,
  - la serie `server_error` de *Tasa de Error por Tipo de Error*.
- Kibana: `kubernetes.labels.app : "pharmago-api-gateway" and Message : "Connection refused"`, y después `Circuit breaker ABIERTO para pharmacy-cluster`.
- Jaeger: trazas del gateway hacia pharmacy con error o incompletas.
- El gateway renueva sus conexiones con cada servicio cada 15 s (`PooledConnectionLifetimeSeconds`). Así el corte aparece como falla en pocos segundos: en la prueba, el primer error llegó a los 8 s y enseguida se abrió el circuito. Sin tráfico por el gateway no hay síntoma en Grafana: confirmar con K8 contra el Service.

**Diagnóstico**

Identificar qué comunicación está afectada y revisar los Services:

1. `kubectl get svc pharmago-pharmacy-service -n pharmago -o jsonpath='{.spec.ports[0].targetPort}'` → tiene que dar **80**.
2. `kubectl get endpoints pharmago-pharmacy-service -n pharmago` → las IP tienen que estar en `:80`. Con el corte aparecen en `:1`.
3. K6: muestra en qué difiere el Service del manifiesto.
4. K8 contra `http://pharmago-pharmacy-service/health` (directo) y contra `http://pharmago-api-gateway/api/drug` (por el gateway).
5. Descartar otras causas:
   - `kubectl get networkpolicy -n pharmago`: el repo no define ninguna, así que cualquiera que aparezca fue inyectada.
   - K2 tiene que mostrar pharmacy en `2/2`. Si no, es el escenario `component`.

**Qué hace el sistema solo**

- El gateway reintenta 2 veces (`GET`, `HEAD`, `OPTIONS`) con backoff de 100 y 200 ms más un jitter de hasta 100 ms.
- Con 5 fallos seguidos, el circuit breaker se abre y el gateway responde `503` al instante durante 5 s. Después deja pasar 1 request de prueba cada 5 s.
- **No corrige el Service.** Lo restaura el bloque `finally` del script al terminar `--duration`.
- Observado (2026-11-03): corte de 22:19:24 a 22:19:48 UTC, con **20 de 20 sondas fallidas**. Después de restaurar, **12 de 12 `200` inmediatas**.

**Mitigación manual** (revertir la configuración que corta la red)

| Acción | Comando | Riesgo |
|---|---|---|
| Restaurar el puerto | `kubectl patch svc pharmago-pharmacy-service -n pharmago --type=json -p='[{"op":"replace","path":"/spec/ports/0/targetPort","value":80}]'` | 🟢 Vuelve al valor del manifiesto. Si el script sigue corriendo, su propia restauración falla (su operación `test` espera `1`), sin afectar al servicio. |
| Volver a aplicar el manifiesto | `kubectl apply -f services/backend/pharmacy-service-service.yaml` | 🟢 |
| Borrar una NetworkPolicy inyectada | `kubectl delete networkpolicy <nombre> -n pharmago` | 🟠 Confirmar con el IC que no es nuestra. |

**Validación:**
- El `targetPort` vuelve a `80` y `endpoints` muestra `:80`.
- K8 devuelve `200` por las dos rutas y Q1 = 0.
- Si el circuit breaker se había abierto, Kibana muestra `Circuit breaker CERRADO para pharmacy-cluster`.

**Severidad típica:**
- SEV1 si se confirma que el flujo de compras no funciona por el gateway. Q1 > 25 % o un fallo de K8 contra `/api/drug` orientan el diagnóstico, pero no sustituyen la prueba de compras.
- SEV2 si solo falla el acceso directo al Service.

---

### 7.6 Desconexión de componentes internos (`component`)

**Inyección (A):** el script escala `pharmago-pharmacy-service` de 2 a **0 réplicas** durante `--duration` y después restaura la cantidad original.

Otras variantes posibles: borrar pods, matar el proceso, o bajar `pharmago-users-service` o `pharmago-db`.

**Síntoma**

- Alerta: `Errores 5xx en el gateway` (`critical`). Salta ~70 s después de apagar pharmacy y se resuelve ~1 min después de restaurarlo.
- Respuestas `502`, `503` o `504`.
- Grafana, *PharmaGo - Overview*:
  - *Tasa de Error por Endpoint (%)* sube en `/api/drug`, `/api/pharmacy` y `/api/purchases`,
  - aparece la serie `server_error` en *Tasa de Error por Tipo de Error*.
- Kibana: `kubernetes.labels.app : "pharmago-api-gateway" and Message : "Circuit breaker ABIERTO"`.

**Diagnóstico**

1. Identificar el componente desconectado. En K2, el deployment que no está en `2/2`:
   - `0/0`: lo escalaron a 0,
   - `0/2` o `1/2`: los pods están cayendo o reiniciando.
2. K1 y K4: buscar pods en `CrashLoopBackOff`, `Terminating` o `Pending`.
3. Q3: ¿qué servicio devuelve 5xx? Si users **y** pharmacy responden `503`, sospechar de la base: `kubectl get pods -n pharmago -l app=pharmago-db` y buscar `"Database unavailable"` en Kibana.
4. K6: ¿cambió `replicas` u otro campo respecto del manifiesto?
5. Comprobar que el circuit breaker esté limitando las llamadas: `503` inmediatos con el header `X-Circuit-Breaker: open`, y en Kibana el log `Circuit breaker ABIERTO`.

**Qué hace el sistema solo**

- **Escalado a 0:** Kubernetes respeta el valor nuevo y **no** lo revierte. El circuit breaker evita que los requests queden esperando: los primeros dan `502` y después `503` inmediato. Lo restaura el script al terminar `--duration`.
- **Un pod borrado o caído:** la otra réplica atiende y el Deployment crea un pod nuevo. MTTR observado:
  - pharmacy, pod borrado: **6.8 s**,
  - users, pod borrado: **5.6 s**,
  - proceso muerto: **~10.7 s**, hasta 18.9 s cuando entra en juego el backoff de `CrashLoopBackOff`.
  
  Limitación conocida: puede fallar 1 request en el primer segundo.
- **Base caída:** users y pharmacy responden `503 Database unavailable` y el circuito se abre en los dos clusters. El readiness no chequea la base a propósito.
- Observado (2026-11-03):
  - 2 → 0 réplicas a las 22:20:13 UTC: **17 de 17 fallidas** (2 × `502` y después `503`).
  - Restauración a las 22:20:44, y **11 de 11 `200`** desde las 22:20:56: ~12 s, contando el rollout.
  - En la prueba del circuit breaker hubo ~5 s de `503` después de restaurar, hasta que el circuito volvió a probar.

**Mitigación manual** (restablecer el componente)

| Acción | Comando | Riesgo |
|---|---|---|
| Restaurar las réplicas de pharmacy | `kubectl scale deployment/pharmago-pharmacy-service -n pharmago --replicas=2`, después `kubectl rollout status deployment/pharmago-pharmacy-service -n pharmago --timeout=180s` | 🟢 |
| Restaurar las réplicas de users | `kubectl scale deployment/pharmago-users-service -n pharmago --replicas=2` | 🟢 |
| Volver al manifiesto | `kubectl apply -f deployments/backend/pharmacy-service-deployment.yaml` | 🟢 |
| Reiniciar pods trabados | `kubectl rollout restart deployment/<deployment> -n pharmago` | 🟢 |
| Rollback de un cambio de configuración | `kubectl rollout undo deployment/<deployment> -n pharmago` (antes, ver `kubectl rollout history deployment/<deployment> -n pharmago`) | 🟠 Solo revierte el pod template (env, probes, límites). **No revierte el código**: las imágenes son `:latest` con `imagePullPolicy: Never`. |
| Base escalada a 0 | `kubectl scale deployment/pharmago-db -n pharmago --replicas=1` | 🟠 Corte total hasta que pase la readiness (initialDelay de 30 s). Los datos persisten en el PV. |
| Reiniciar la base | `kubectl rollout restart deployment/pharmago-db -n pharmago` | 🔴 Tiene una sola réplica: corte total. Solo si está colgada y lo decide el IC. |

**Validación:**
- K2 en `2/2` (la base en `1/1`).
- K8 contra `/api/drug` y `/test-health` devuelve `200`, Q1 = 0 y se prueba la operación afectada (login o compra) con datos de prueba.
- Kibana muestra `Circuit breaker CERRADO para pharmacy-cluster`.
- K1 no muestra reinicios nuevos.

**Severidad típica:**
- SEV1: pharmacy en 0 (sin compras), users en 0 (sin login) o base caída (todo).
- SEV4: un solo pod caído con la otra réplica atendiendo.

---

## 8. Verificación de la recuperación

Un incidente se da por **resuelto** solo cuando se cumple **todo** esto:

- [ ] Los componentes afectados están operativos y sus pods están `READY` (K1).
- [ ] Los Deployments tienen las réplicas esperadas (K2: backend `2/2`, base y UI `1/1`).
- [ ] Las sondas básicas responden: K8 contra `/api/drug` (lectura del catálogo) y `/test-health` (salud de users) devuelve `200`. Si se afectó login o compras, verificar además la operación real de punta a punta con datos de prueba.
- [ ] Las métricas volvieron a la línea base: Q1 = 0, latencia ~5 ms, CPU y memoria en sus valores previos.
- [ ] No quedan errores del incidente: Kibana muestra `Circuit breaker CERRADO` si se había abierto, y no hay reintentos nuevos.
- [ ] Las medidas temporales están revertidas: réplicas del gateway de vuelta en 2, variables de entorno quitadas, pods auxiliares y de caos borrados (K3 vacío).

**Que la alerta vuelva a Normal no alcanza por sí solo para declarar la recuperación.** Las alertas se atrasan respecto del estado real: la de 5xx se resuelve ~1 min después de restaurar, y las de infraestructura usan `for: 2m` y ventanas de 5 min.

### Indicadores

| Indicador | Definición en este plan | Cómo se mide |
|---|---|---|
| **MTTD** | T_detección − T_inicio | Registro (sección 9) |
| **MTTA** | T_reconocimiento − T_detección | Registro |
| **MTTR** | T_restauración − T_inicio | Registro; además, `measure-mttr.sh` para la caída de pods |
| **Tasa de errores** | Porcentaje de solicitudes que fallan durante el incidente | Q1 (máximo durante el incidente) |
| **Disponibilidad** | Porcentaje de solicitudes atendidas bien en el período | `ok / total` en `kubectl logs -n pharmago war-room-traffic` |

Se comparan entre escenarios para evaluar los mecanismos y encontrar mejoras.

---

## 9. Plantilla de registro de incidente

Copiar una por incidente. La llena el RO **mientras ocurre**, en hora local (UTC-3).

```
INCIDENTE INC-___              Escenario sospechado: ___________
IC: ________   RT: ________   RO: ________
Estado: [ ] INVESTIGANDO  [ ] CONTENIDO  [ ] RECUPERÁNDOSE  [ ] RESUELTO

T_inicio (inyección, según docentes o log del script UTC-3h): __:__:__
T_detección (alerta o panel que lo muestra):                  __:__:__   Fuente: ____________
T_reconocimiento (IC declara SEV):                            __:__:__
T_mitigación (primera acción manual, si hubo):                __:__:__
T_restauración (verificación OK):                             __:__:__
T_cierre:                                                     __:__:__

Descripción (1 línea): ____________________________________________
Componente afectado: [ ] gateway [ ] users [ ] pharmacy [ ] base [ ] UI [ ] solo infra (nodo ______)
Impacto:             [ ] login  [ ] compras  [ ] catálogo  [ ] ninguno visible
Severidad inicial: SEV_   Severidad final: SEV_   Motivo del cambio: ____________

Acciones (una por línea: hora, responsable, comando o decisión, resultado)
hh:mm:ss | resp. | acción | resultado
__:__:__ |       |        |
__:__:__ |       |        |
__:__:__ |       |        |

Mecanismo automático que actuó: [ ] probes/reinicio [ ] réplicas [ ] circuit breaker
                                [ ] reintentos [ ] rate limiting [ ] 503 por base [ ] ninguno

Indicadores
MTTD = T_detección − T_inicio             = ____ s
MTTA = T_reconocimiento − T_detección     = ____ s
MTTR = T_restauración − T_inicio          = ____ s
Tasa de errores máx. (Q1)                 = ____ %
Disponibilidad durante el incidente       = ____ %  (requests OK / total del tráfico de fondo)
```

---

## 10. Análisis posterior al incidente (postmortem)

Se hace después de cada incidente SEV1 o SEV2, y de cualquier otro que deje aprendizajes, dentro de las 24 h siguientes.

El enfoque es **blameless**: se habla de sistemas y procesos, no de personas ("la alerta no saltó porque…", no "X no vio…"). Lo que sale del postmortem se usa para actualizar este plan.

```
# Postmortem — INC-___ (<fecha>)   SEV_   Autor: ______   Revisado por: ______

## Resumen
2–3 líneas: qué pasó, cuánto duró y a quién afectó.

## Impacto
- Componentes y funcionalidades afectadas:
- Duración del impacto (T_inicio → T_restauración):
- Tasa de errores máxima (Q1) / disponibilidad:
- MTTD / MTTA / MTTR:

## Línea de tiempo (UTC-3)
| Hora | Evento |
|---|---|
|  | Inicio |
|  | Detección (fuente) |
|  | SEV declarada |
|  | Contención |
|  | Recuperación verificada |

## Causa
Origen identificado y factores contribuyentes (5 porqués): por qué pasó y por qué el sistema no lo absorbió del todo.

## Contención y recuperación
Acciones realizadas y su resultado.

## Evaluación
- Qué funcionó (ej. el circuit breaker respondió 503 al instante; el rolling restart no cortó el servicio):
- Limitaciones detectadas / dónde tuvimos suerte (ej. la alerta no saltó por `for: 2m`):

## Acciones preventivas
| # | Mejora | Tipo (prevenir / detectar / mitigar) | Responsable | Plazo |
|---|---|---|---|---|
| 1 |  |  |  |  |
```

---

## 11. Validación mediante ingeniería del caos

La efectividad del plan se evalúa con experimentos controlados de los seis escenarios, usando `run-chaos.py`. El script está documentado en [chaos-engineering/README.md](../../../Implementacion%20K8S/Codigo/chaos-engineering/README.md).

**Antes de cada prueba:**
- verificar el estado inicial (checklist 12.3),
- definir el alcance de la falla (`--scenario`),
- definir la duración máxima (`--duration`, de 5 a 120 s),
- tener a mano el procedimiento de recuperación (final de la [sección 13](#13-referencia-rápida-consultas-y-comandos)).

**Durante la ejecución se registra** (plantilla de la sección 9):
- tipo de falla y componente afectado,
- hora de inicio y de fin,
- alertas activadas y cambios en las métricas,
- errores y trazas,
- respuesta de los mecanismos automáticos,
- acciones de contención,
- tiempo hasta recuperar el funcionamiento.

**Al terminar:**
- detener la inyección y revertir lo temporal,
- comprobar que el sistema volvió a su estado inicial (sección 8),
- comparar el resultado con lo esperado en cada runbook.

Corridas registradas: [07-scripts-caos](../07-scripts-caos/) (los seis escenarios y las métricas de CPU, memoria y storage) y [02-alta-disponibilidad](../02-alta-disponibilidad/) (circuit breaker y MTTR).

---

## 12. Checklist para el día de la defensa

### 12.1 Roles

Se confirman al empezar cada incidente ([sección 2](#2-roles-y-responsabilidades)). Por defecto: IC Federico Galusso · RT Juan Diego Fagnoni · RO Juan Pedro Michelini. Canal principal: Microsoft Teams; alternativo: llamada telefónica grupal.

### 12.2 Qué tener abierto

- [ ] Terminal 1 (Bash o zsh, en `Implementacion K8S/Codigo/k8s`): `./port-forward.sh`.
- [ ] Terminal 2 (RT): `kubectl get pods -n pharmago -o wide -w`.
- [ ] Terminal 3 (RT): libre para diagnóstico y mitigación.
- [ ] Grafana http://127.0.0.1:3000 (usuario `admin`; verificar la contraseña vigente antes de la defensa): *PharmaGo - Overview* y *PharmaGo - Infra*, rango `Last 15 minutes`, refresh `5s`.
- [ ] Grafana → Alerting → Alert rules → carpeta **PharmaGo** (7 reglas: 5 de infraestructura y 2 de servicio).
- [ ] Prometheus http://127.0.0.1:9090 → Graph, con Q1, Q3 y Q6 cargadas en pestañas.
- [ ] Kibana http://127.0.0.1:5601 → Discover: data view `pharmago-logs-*`, filtro `kubernetes.labels.app : "pharmago-api-gateway"`, últimos 15 min, auto-refresh.
- [ ] Jaeger http://127.0.0.1:16686: servicio `PharmaGo.ApiGateway`.
- [ ] Este documento y una copia en blanco de la [plantilla de registro](#9-plantilla-de-registro-de-incidente).
- [ ] Tráfico de fondo. Hace falta para que `network` y `component` se vean en Grafana y para medir la disponibilidad:

```bash
kubectl run war-room-traffic -n pharmago --image=busybox:1.36 --image-pull-policy=IfNotPresent \
  --restart=Never --overrides='{"spec":{"nodeSelector":{"node-type":"ops"}}}' -- sh -c '
i=0; while true; do
  case $((i % 3)) in 0) p=/api/drug ;; 1) p=/api/pharmacy ;; 2) p=/test-health ;; esac
  wget -q -T 3 -O /dev/null "http://pharmago-api-gateway$p" && r=ok || r=FAIL
  echo "$(date +%H:%M:%S) $r $p"; i=$((i + 1)); sleep 4
done'
# Ver resultados: kubectl logs -n pharmago war-room-traffic --tail=20
# Disponibilidad: kubectl logs -n pharmago war-room-traffic | grep -c " ok "  (sobre el total de líneas)
# Al terminar:    kubectl delete pod war-room-traffic -n pharmago
```

Son 15 req/min, por debajo del rate limiting. Replica el tráfico de la [línea base](../00-linea-base/06-metricas-linea-base.txt).

### 12.3 Cluster sano antes de arrancar

Después de iniciar Docker, ejecutar `minikube start -p pharmago` y `minikube update-context -p pharmago` antes de usar `kubectl`. El puerto local del API server puede cambiar al reiniciar Docker; no editarlo a mano en `kubeconfig`. Luego iniciar `./port-forward.sh` una sola vez para los accesos web.

| Chequeo | Comando | Esperado |
|---|---|---|
| Nodos | `kubectl get nodes -L node-type` | 5 `Ready`; `pharmago-m02` = `ops`; `pharmago`, `pharmago-m03` y `pharmago-m04` = `backend` |
| Deployments | K2 | gateway, users y pharmacy `2/2`; `pharmago-db` y `pharmago-ui` `1/1` |
| Ubicación | `kubectl get pods -n pharmago -l 'workload-class=pharmago-backend-microservice' -o wide` | Users en `pharmago`, gateway en `pharmago-m03`, pharmacy en `pharmago-m04` |
| Reinicios | K1 | `RESTARTS` estable (anotar el valor inicial) |
| Sin restos de caos | K3 | `No resources found` |
| Service de pharmacy | `kubectl get svc pharmago-pharmacy-service -n pharmago -o jsonpath='{.spec.ports[0].targetPort}'` | `80` |
| Sin cambios respecto del repo | K6 | Sin diferencias (o solo anotaciones de `rollout restart`) |
| Rutas de comprobación básica | K8 con `/api/drug` y `/test-health` | `HTTP/1.1 200 OK` |
| Errores | Q1 | 0 |
| Alertas | Grafana → Alert rules | Las 7 en **Normal** |
| metrics-server (para K7) | `kubectl top nodes` | Valores para los 5 nodos. Si da `Metrics API not available`, activar el addon: `minikube addons enable metrics-server -p pharmago` (tarda ~1 min en tener datos). Mientras tanto, usar Q6 y Q7. |
| Mapeo de `Message` en Kibana | Kibana → Dev Tools: `GET pharmago-logs-*/_field_caps?fields=Message,Message.keyword` | `Message` = `text` y `Message.keyword` = `keyword` (verificado el 2026-11-14 en todos los índices) |
| Tráfico de fondo corriendo | `kubectl logs -n pharmago war-room-traffic --tail=6` | Líneas `ok` para `/api/drug`, `/api/pharmacy` y `/test-health` |
| Logs | Kibana | Hay eventos del gateway de los últimos 2 min |

---

## 13. Referencia rápida: consultas y comandos

### PromQL (Prometheus o Grafana → Explore)

Las consultas usan `[1m]` para reaccionar rápido. Los paneles usan `[5m]` y **suavizan** los picos cortos: 30 s con 100 % de errores se ven como ~10 % en el panel.

| ID | Qué mide | Consulta |
|---|---|---|
| Q1 | Tasa de 5xx del gateway (%), sin contar las probes. Es la misma base que la alerta `Errores 5xx en el gateway`, sin el mínimo de 3 errores. | `100 * sum(rate(pharmago_http_requests_total{job="PharmaGo.ApiGateway",endpoint!="/health",status_code=~"5.."}[1m])) / sum(rate(pharmago_http_requests_total{job="PharmaGo.ApiGateway",endpoint!="/health"}[1m]))` |
| Q2 | Req/s por endpoint en el gateway. La alerta de pico usa la suma total (`> 2`). | `sum by (endpoint) (rate(pharmago_http_requests_total{job="PharmaGo.ApiGateway"}[1m]))` |
| Q3 | Códigos HTTP por servicio | `sum by (job, status_code) (rate(pharmago_http_requests_total[1m]))` |
| Q4 | `429` por endpoint | `sum by (endpoint) (rate(pharmago_http_requests_total{status_code="429"}[1m]))` |
| Q5 | Latencia promedio en el gateway (ms) | `sum by (endpoint) (rate(pharmago_http_request_duration_milliseconds_sum{job="PharmaGo.ApiGateway"}[1m])) / sum by (endpoint) (rate(pharmago_http_request_duration_milliseconds_count{job="PharmaGo.ApiGateway"}[1m]))` |
| Q6 | CPU por pod (núcleos). Mismo filtro que la alerta `CPU de pod alta`. | `sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="pharmago",id=~".*pod[^/]*"}[1m]))` |
| Q7 | Memoria por pod (MiB) | `sum by (pod) (container_memory_working_set_bytes{namespace="pharmago",id=~".*pod[^/]*"}) / 1048576` |
| Q8 | Storage efímero por pod (MiB) | `k8s_pod_filesystem_usage_bytes{k8s_namespace_name="pharmago"} / 1048576` |
| Q9 | CPU de nodo (%), igual que la alerta | `100 * (1 - avg by (node) (rate(node_cpu_seconds_total{mode="idle"}[5m])))` |
| Q10 | Memoria de nodo (%), igual que la alerta | `100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)` |
| Q11 | Disco disponible (%), igual que la alerta | `min by (node) (100 * node_filesystem_avail_bytes{mountpoint=~"/\|/data",fstype!~"tmpfs\|overlay\|squashfs"} / node_filesystem_size_bytes{mountpoint=~"/\|/data",fstype!~"tmpfs\|overlay\|squashfs"})` |

En Minikube, las series de cAdvisor no tienen la etiqueta `container`. Por eso Q6 y Q7 filtran por el cgroup del pod (`id` que termina en el segmento del pod): así no se suman dos veces el pod y sus contenedores. Es el mismo arreglo que el PR #29 aplicó a la alerta, que antes quedaba en `nodata`. El panel *CPU por pod (pharmago)* no aplica ese filtro y puede mostrar valores más altos: para cifras exactas, usar Q6.

En el archivo crudo, los `|` de las expresiones regulares están escapados como `\|` para no romper la tabla; en GitHub se ven bien. Si se copia desde el `.md`, sacar la barra invertida.

### KQL (Kibana, data view `pharmago-logs-*`)

| Busca | KQL |
|---|---|
| Circuit breaker | `kubernetes.labels.app : "pharmago-api-gateway" and Message : "Circuit breaker"` |
| Reintentos | `kubernetes.labels.app : "pharmago-api-gateway" and Message : "Reintento"` |
| Conexión rechazada | `kubernetes.labels.app : "pharmago-api-gateway" and Message : "Connection refused"` |
| Base caída | `"Database unavailable"` |
| Errores de pharmacy | `kubernetes.labels.app : "pharmago-pharmacy-service" and LogLevel : "Error"` |
| Errores de users | `kubernetes.labels.app : "pharmago-users-service" and LogLevel : "Error"` |

`Message` es `text` (sirve la búsqueda por frase) y `Message.keyword` es `keyword` (solo coincidencia exacta); verificado el 2026-11-14 con `_field_caps` en todos los índices `pharmago-logs-*`. Los logs del circuit breaker y de los reintentos recién aparecen cuando hay una falla: antes de la defensa no hay eventos para probar esas búsquedas.

### kubectl

| ID | Para qué | Comando |
|---|---|---|
| K1 | Pods, nodo y reinicios | `kubectl get pods -n pharmago -o wide` |
| K2 | Réplicas por deployment | `kubectl get deploy -n pharmago` |
| K3 | Pods de caos | `kubectl get pods -n pharmago -l app.kubernetes.io/name=pharmago-chaos -o wide --show-labels` |
| K4 | Últimos eventos | `kubectl get events -n pharmago --sort-by=.lastTimestamp \| tail -20` |
| K5 | Logs del gateway | `kubectl logs -n pharmago -l app=pharmago-api-gateway --tail=50 --prefix` |
| K6 | Diferencias con el repo | `kubectl diff -f services/backend/ -f deployments/backend/` |
| K7 | Consumo en vivo | `kubectl top pods -n pharmago` (requiere el addon metrics-server; ver 12.3) |
| K8 | Prueba desde dentro del cluster | `kubectl run war-room-check -n pharmago --rm -i --restart=Never --image=busybox:1.36 --image-pull-policy=IfNotPresent -- wget -S -q -T 3 -O /dev/null http://pharmago-api-gateway/api/drug` (cambiar la URL según el caso) |

Restauración completa si `run-chaos.py` se interrumpe (tomada de [chaos-engineering/README.md](../../../Implementacion%20K8S/Codigo/chaos-engineering/README.md#restauración)):

```bash
kubectl patch svc pharmago-pharmacy-service -n pharmago --type=json \
  -p='[{"op":"replace","path":"/spec/ports/0/targetPort","value":80}]'
kubectl scale deployment/pharmago-pharmacy-service -n pharmago --replicas=2
kubectl rollout status deployment/pharmago-pharmacy-service -n pharmago --timeout=180s
kubectl delete pods -n pharmago -l app.kubernetes.io/name=pharmago-chaos --ignore-not-found
```

---

## Evidencias

La aplicación del plan se documenta en [docs/evidencia](../README.md) con:

- capturas de Grafana,
- métricas de Prometheus,
- logs de Kibana,
- trazas de Jaeger,
- eventos de Kubernetes,
- salidas de los scripts de caos.

Tienen que alcanzar para reconstruir cada incidente, identificar las acciones realizadas y demostrar que el sistema se recuperó. Lo que se genere en la defensa va en `evidencia/08-defensa/`.

## Conclusión

Este plan organiza la respuesta ante incidentes que comprometen el funcionamiento de PharmaGo:

- aprovecha los mecanismos de recuperación y resiliencia que ya existen (réplicas, probes, circuit breaker, reintentos, rate limiting),
- aprovecha la observabilidad para detectar, limitar el impacto y restablecer el servicio,
- hace explícitas sus limitaciones: alertas que no alcanzan a saltar en cargas cortas, y réplicas que comparten nodo.

La validación con ingeniería del caos permite comprobar si funciona y proponer mejoras basadas en lo observado.

## Referencias

- [run-chaos.py](../../../Implementacion%20K8S/Codigo/chaos-engineering/run-chaos.py) y su [README](../../../Implementacion%20K8S/Codigo/chaos-engineering/README.md)
- [grafana-alerts.yaml](../../../Implementacion%20K8S/Codigo/k8s/configmaps/grafana-alerts.yaml)
- [README-k8s.md](../../../Implementacion%20K8S/Codigo/k8s/README-k8s.md): "Despliegue sin caída", "Resiliencia ante fallas" y "Acceso a los Servicios"
- [README-telemetry.md](../../../Implementacion%20K8S/Codigo/k8s/README-telemetry.md)
- Evidencia: [00-linea-base](../00-linea-base/), [02-alta-disponibilidad](../02-alta-disponibilidad/), [07-scripts-caos](../07-scripts-caos/)
