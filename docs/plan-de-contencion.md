# Plan de contención de incidentes operacionales — PharmaGo

Ítem 6 de la rúbrica. Para usar **en vivo** en el war-room. Ciclo: **Prepare → Detect → Respond → Recover → Learn**.

- Comandos: se corren en **Git Bash**, desde `Implementacion K8S/Codigo/k8s`, con namespace `pharmago`.
- Horas: Grafana y el registro van en hora local (UTC-3). `run-chaos.py` imprime **UTC** (sumar 3 h a la hora local para comparar).
- `Qn` = consulta PromQL y `Kn` = comando kubectl de la [sección 10](#10-referencia-rápida-consultas-y-comandos).

---

## Resumen: escenario → respuesta

| Escenario | Alerta que salta | Mecanismo automático | Mitigación manual | SEV típica |
|---|---|---|---|---|
| `requests` | `[[ALERTA PENDIENTE: pico de requests]]` | Rate limiting del gateway → `429` | Borrar el pod que inunda; escalar el gateway si hay 5xx | SEV3 |
| `cpu` | `CPU de pod alta` / `CPU de nodo alta` (solo si la carga dura más de ~4 min) | Límite de CPU del pod; liveness probe | Borrar el pod de carga o el pod afectado | SEV3 |
| `memory` | `Memoria de nodo alta` (solo con más de 90 % de uso) | Límite de memoria → OOMKilled y reinicio | Borrar el pod de carga o el pod afectado | SEV3 |
| `storage` | `Espacio de disco bajo` (solo con menos de 15 % libre) | `sizeLimit` / límite de `ephemeral-storage` → desalojo | Borrar el pod de carga o el archivo | SEV3 |
| `network` | `[[ALERTA PENDIENTE: tasa de 5xx]]` (`Errores de red de nodo` **no** salta) | Reintentos y circuit breaker en el gateway | Restaurar `targetPort` 80 del Service | SEV1–SEV2 |
| `component` | `[[ALERTA PENDIENTE: tasa de 5xx]]` | Circuit breaker → `503` inmediato; 2 réplicas y probes si muere un pod | `kubectl scale ... --replicas=2` | SEV1 |

> **Ojo:** las 5 alertas actuales usan `for: 2m`, y las de CPU de pod y nodo además `rate(...[5m])`. Con los valores por defecto del script (`--duration 20`, `96` MiB, `128` MiB) **ninguna llega a dispararse**. En `cpu`, `memory` y `storage`, la detección real se hace con los paneles de *PharmaGo - Infra* y con K3.

---

## 1. Alcance y servicios críticos

**Cubre:** incidentes operacionales del cluster Minikube `pharmago` (5 nodos, namespace `pharmago`), es decir, los 6 escenarios de `run-chaos.py` y sus variantes. **No cubre:** seguridad ni pérdida de datos de la base.

| Componente | Deployment / Service | Nodo | Réplicas | Depende de | Si falla… |
|---|---|---|---|---|---|
| UI (Angular) | `pharmago-ui` | `pharmago-m03` | 1 | gateway | No carga el front |
| API Gateway (YARP) | `pharmago-api-gateway` | `pharmago-m03` | 2 | users, pharmacy | **Todo** cae |
| Users | `pharmago-users-service` | `pharmago` | 2 | base | Cae el **login** (`/api/login`, `/api/users`, `/api/roles`, `/api/invitations`) |
| Pharmacy | `pharmago-pharmacy-service` | `pharmago-m04` | 2 | base | Caen las **compras** y el catálogo (`/api/purchases`, `/api/drug`, `/api/pharmacy`, `/api/stockrequest`, `/api/unitmeasure`) |
| SQL Server | `pharmago-db` | `pharmago-m02` (ops) | 1 | PV `hostPath` | users y pharmacy responden `503 Database unavailable` |
| Observabilidad | `prometheus`, `grafana`, `kibana`, `elasticsearch`, `otlp-collector` | `pharmago-m02` | 1 | — | Perdemos visibilidad, no servicio |

Cadena crítica: **UI → gateway → users / pharmacy → base**. La base es el único punto único de falla de negocio. Las 2 réplicas de cada servicio comparten nodo, así que cubren la caída de un pod pero no la de su nodo.

---

## 2. Roles del war-room

| Rol | Persona | Hace | No hace |
|---|---|---|---|
| **Incident Commander (IC)** | `________` | Declara el incidente y la SEV, asigna tareas, decide qué mitigación se aplica, da por cerrado el incidente y **explica a los docentes**. | No ejecuta comandos. |
| **Responder** | `________` | Corre los diagnósticos (K*, Q*), propone la causa y ejecuta la mitigación **cuando el IC la aprueba**. Anuncia en voz alta cada comando que cambia algo. | No decide solo una mitigación con riesgo. |
| **Comunicaciones / escriba** | `________` | Lleva el registro ([sección 7](#7-plantilla-de-registro-de-incidente)) con hora de cada acción, tiene Grafana y Kibana en pantalla, lee los valores en voz alta y manda las actualizaciones de estado. | No corre comandos que modifiquen el cluster. |

Si el IC queda bloqueado, el escriba asume como IC y el IC pasa a responder.

---

## 3. Niveles de severidad

### Cuándo se declara un incidente

Alcanza con **una** de estas condiciones:

1. Cualquier alerta de *Grafana → Alerting → Alert rules → PharmaGo* en estado **Firing**.
2. Tasa de 5xx del gateway (Q1) **> 1 % durante 1 min**.
3. Un deployment backend con menos réplicas disponibles que las deseadas en K2 (`READY` distinto de `2/2`).
4. Un pod con `app.kubernetes.io/name=pharmago-chaos` en K3, o un pod que no está en los manifiestos.
5. Una validación desde dentro del cluster (K8) que no devuelve `200`.

### Criterios

| | **SEV1 — crítico** | **SEV2 — degradado** | **SEV3 — menor** |
|---|---|---|---|
| Tasa de 5xx del gateway (Q1) | > 25 % | 1–25 % | < 1 % |
| Funcionalidad | Login **o** compras no disponibles | Una ruta secundaria falla; login y compras funcionan | Ninguna ruta falla |
| Servicios afectados | Gateway, base o ≥ 2 servicios; o users o pharmacy con 0 réplicas disponibles | 1 servicio con una réplica menos o con errores intermitentes | Solo recursos (CPU, memoria, disco) o `429` |
| Latencia promedio (panel *Latencia Promedio por Endpoint*) | — | > 1000 ms (umbral rojo del panel) | Por encima de la línea base (~5 ms) pero < 1000 ms |
| Reconocer (ack) | ≤ 1 min | ≤ 2 min | ≤ 5 min |
| Empezar a mitigar | ≤ 3 min | ≤ 10 min | Cuando lo indique el IC |
| Actualización de estado | Cada 2 min | Cada 5 min | Al abrir y al cerrar |

Ante la duda entre dos niveles, se elige el **más alto**, y se baja cuando se confirma el impacto.

**Se sube la SEV** cuando:

- se suma otro componente afectado,
- lo automático no alcanza (por ejemplo, pasan 2 min sin que las réplicas vuelvan a `READY`),
- hay riesgo para los datos de la base,
- la mitigación no reduce Q1.

**Ojo con el panel *Tasa de Error por Endpoint (%)*:** cuenta 4xx y 5xx, así que un pico de `429` aparece como "error". Para decidir la SEV se usa Q1, que cuenta solo 5xx.

---

## 4. Canal y protocolo de comunicación

- **Canal único:** `________` (ej. grupo del equipo). Todo lo que se decide se dice en voz alta en la sala **y** queda en el registro. No se abren canales paralelos.
- **Quién habla con los docentes:** el IC.
- **Frecuencia:** según la SEV (tabla anterior).
- **Formato del mensaje de estado** (lo escribe el escriba y lo lee el IC):

```
[SEV#] [ABIERTO | MITIGANDO | MONITOREANDO | CERRADO] hh:mm
Impacto: <qué no funciona para el usuario, ej. "compras fallan con 503">
Servicios: <deployments afectados>
Causa probable: <hipótesis actual o "en diagnóstico">
Acción en curso: <qué se está haciendo y quién>
Próxima actualización: hh:mm
```

---

## 5. Flujo general de respuesta

| # | Paso | Quién | Cómo |
|---|---|---|---|
| 1 | **Confirmar** | Escriba + Responder | Alerta o panel → ver el panel en Grafana y correr K1, K2 y K3. ¿Es real o es ruido? Anotar **T_detección**. |
| 2 | **Declarar severidad** | IC | Tabla de la [sección 3](#3-niveles-de-severidad). Anotar **T_reconocimiento**. Primer mensaje de estado. |
| 3 | **Asignar roles** | IC | Confirmar quién es Responder y quién escriba. Abrir el runbook de la [sección 6](#6-runbooks). |
| 4 | **Diagnosticar** | Responder | Pasos "Diagnóstico" del runbook. Confirmar la causa **y descartar** las otras (K6 descarta cambios en manifiestos). |
| 5 | **Mitigar** | Responder (aprueba el IC) | Primero se espera lo automático (columna "Qué hace el sistema solo"). Si no alcanza, mitigación manual: primero las 🟢, las 🟠 solo con OK del IC. |
| 6 | **Validar la recuperación** | Escriba + Responder | Pasos "Validación" del runbook. Que la alerta vuelva a **Normal** no alcanza: hay que confirmar con K2, K8 y Q1. Anotar **T_restauración**. |
| 7 | **Cerrar** | IC | Revertir las medidas temporales (por ejemplo, el gateway escalado vuelve a 2 réplicas). Último mensaje `CERRADO`. Calcular MTTD, MTTA y MTTR. Borrar los pods auxiliares (K8, tráfico de fondo). |
| 8 | **Postmortem** | Los 3 | Plantilla de la [sección 8](#8-plantilla-de-postmortem-blameless), en las 24 h siguientes. |

Riesgo de las mitigaciones: 🟢 seguro (reversible, sin corte) · 🟠 con riesgo (puede cortar servicio o no ser reversible) · 🔴 evitar en vivo.

---

## 6. Runbooks

Hay dos formas de inyectar caos:

- **A — `run-chaos.py`** (el del repo): crea pods temporales `pharmago-chaos-<escenario>-xxxx` con la etiqueta `app.kubernetes.io/name=pharmago-chaos`, en el nodo del gateway (`pharmago-m03`), y sondas en el nodo `ops`. **No toca los pods de la aplicación**, salvo en `network` y `component`.
- **B — scripts originales del curso** (`cpu-spike.sh`, `ram-spike.sh`, `volume-spike.sh`, `load-requests.sh`, eliminados del repo en el commit `bc8c624`): hacen `kubectl exec` dentro del **primer pod** de un deployment (por defecto `pharmago-api-gateway`) y dejan procesos o archivos (`/tmp/chaos_fill`, `/dev/shm/fill`) adentro.

Primer comando siempre: **K3**. Si aparece un pod `pharmago-chaos-*`, es la variante A y su nombre indica el escenario.

### 6.1 `requests` — pico de requests

**Inyección:** A: hasta 200 `GET /api/drug` seguidos desde un pod en `ops`. B: 500 `POST /api/login` en paralelo vía port-forward.

**Síntoma**

- Alerta: `[[ALERTA PENDIENTE: pico de requests]]`.
- Grafana, *PharmaGo - Overview*: suben *Peticiones por Segundo por Endpoint* y *Throughput Total*, y aparecen `429` en *Rate Limit Errors (429) por Endpoint* y en *Tasa de Error - /api/drug (429 y Total)*.
- Kibana: `kubernetes.labels.app : "pharmago-api-gateway"` en el pico.

**Diagnóstico**

1. K3: buscar `pharmago-chaos-requests-*`. Para la variante B, mirar si hay un `kubectl`/`curl` corriendo en la máquina de los docentes contra `127.0.0.1:5000`.
2. Q2: ¿qué endpoint concentra el tráfico? Q4: ¿cuántos `429`/s?
3. Descartar degradación real: Q1 (5xx) tiene que seguir cerca de 0, y Q5 (latencia) por debajo de 1000 ms.
4. Q6: CPU de los pods `pharmago-api-gateway-*` (límite 0.5 núcleos). Si están en el límite, hay saturación.

**Qué hace el sistema solo**

- Rate limiting por IP: 100 req/min y 1000 req/h en todas las rutas; **10 req/min en `POST /api/login`**. `/health` y `/metrics` quedan excluidos. Responde `429`.
- El contador está en memoria de **cada réplica** del gateway (`AddInMemoryRateLimiting`), así que el límite efectivo por IP es de unas 200 req/min.
- Observado (2026-11-03): 200 requests en ~6 s → **197 `200` y 3 `429`**. Sin 5xx.

**Mitigación manual** (solo si hay 5xx o latencia > 1000 ms)

| Acción | Comando | Riesgo |
|---|---|---|
| Cortar la fuente (variante A u otro pod) | `kubectl delete pod <pod-origen> -n pharmago` | 🟢 |
| Escalar el gateway | `kubectl scale deployment/pharmago-api-gateway -n pharmago --replicas=3` | 🟠 Las réplicas tienen afinidad con `pharmago-m03`: si no hay memoria, el pod queda `Pending`. También sube el límite efectivo por IP (~300/min). Volver a 2 al cerrar. |

**Validación:** Q2 vuelve a la línea base (~0.08 req/s por endpoint de negocio), no hay `429` en el panel, Q1 = 0 y K3 queda vacío.

**Severidad típica:** SEV3. Pasa a SEV2 si aparecen 5xx o usuarios legítimos reciben `429` en el login.

---

### 6.2 `cpu` — consumo alto de CPU

**Inyección:** A: pod `pharmago-chaos-cpu-*` con 2 bucles infinitos y límite de 1 CPU, en `pharmago-m03`. B: 4 bucles dentro de un pod `pharmago-api-gateway-*` (límite 0.5 CPU) durante 30 s.

**Síntoma**

- Alerta: `CPU de pod alta` (> 0.4 núcleos, `rate[5m]`, `for: 2m`) o `CPU de nodo alta` (> 85 %, `for: 2m`). Con una carga de 1 núcleo, `CPU de pod alta` recién salta **~4 min después de empezar**. Con el máximo del script (`--duration 120`) no llega a saltar.
- Grafana, *PharmaGo - Infra*: en *CPU por pod (pharmago)*, el pod de carga cerca de 100 (% de un núcleo). En *CPU %*, la serie de `pharmago-m03` sube.
- Si el afectado es el gateway (variante B): sube *Latencia Promedio por Endpoint* en Overview.

**Diagnóstico**

1. K3 → `pharmago-chaos-cpu-*` y su nodo.
2. Q6 (ventana de 1 m, más rápida que el panel): qué pod consume. Q9: CPU de `pharmago-m03`.
3. K7 `kubectl top pods -n pharmago` `[[VERIFICAR]]` (requiere metrics-server, que no está en el repo).
4. Descartar impacto: Q1 = 0 y Q5 < 1000 ms → solo recursos (SEV3).

**Qué hace el sistema solo**

- El pod de carga tiene límite de 1 CPU (A) y los pods de la app de 0.5 CPU: el kernel los limita y no pueden tomar todo el nodo.
- Si `/health` del pod deja de responder, la liveness probe (3 fallos cada 10 s) reinicia el contenedor.
- A: el pod se borra solo al terminar `--duration`. Observado: **pico de 0.997 núcleos** en una corrida de 75 s.

**Mitigación manual**

| Acción | Comando | Riesgo |
|---|---|---|
| Borrar el pod de carga (A) | `kubectl delete pods -n pharmago -l app.kubernetes.io/name=pharmago-chaos --ignore-not-found` | 🟢 |
| Matar los bucles del pod de la app (B): reiniciar ese pod | `kubectl delete pod <pod-afectado> -n pharmago` | 🟢 La otra réplica sigue atendiendo. MTTR observado: 5.6–6.8 s. |
| Reemplazar todos los pods del deployment | `kubectl rollout restart deployment/<deployment> -n pharmago` | 🟢 Rolling update probado sin caída (192 requests, 0 fallidos). |

**Validación:** el pod de carga desaparece de *CPU por pod (pharmago)*, Q6 vuelve a < 0.01 núcleos por pod del backend, K3 queda vacío y la alerta vuelve a **Normal**.

**Severidad típica:** SEV3. Pasa a SEV2 si la latencia supera 1000 ms o se reinicia un pod de la app.

---

### 6.3 `memory` — consumo alto de memoria

**Inyección:** A: pod `pharmago-chaos-memory-*` que llena 96 MiB en `tmpfs` (límite de memoria 120 Mi), en `pharmago-m03`. B: `dd` de 200 MB a `/dev/shm/fill` (o `/tmp/fill`) dentro de un pod `pharmago-api-gateway-*` (límite 384 Mi).

**Síntoma**

- Alerta: `Memoria de nodo alta` (> 90 %, `for: 2m`). Con 96 MiB es improbable que salte.
- Grafana, *PharmaGo - Infra*: el pod de carga aparece en *Memoria por pod (pharmago)* (~96 MiB), y sube la serie de `pharmago-m03` en *Memoria usada %*.

**Diagnóstico**

1. K3 → `pharmago-chaos-memory-*`.
2. Q7: memoria por pod. Línea base: 47–86 MiB por pod del backend.
3. ¿Hubo OOMKilled? Ver la columna `RESTARTS` de K1, y en `kubectl describe pod <pod> -n pharmago`, el campo `Last State: Terminated, Reason: OOMKilled`. Ver también K4.
4. Q10: memoria de `pharmago-m03`.

**Qué hace el sistema solo**

- Límite de memoria: si un contenedor lo supera, Kubernetes lo mata (OOMKilled) y lo reinicia. El archivo en `/dev/shm` o `/tmp` desaparece con el contenedor. La otra réplica sigue atendiendo.
- A: el pod se borra al terminar `--duration`. Observado: **pico de 96.5 MiB** en el pod de carga.

**Mitigación manual**

| Acción | Comando | Riesgo |
|---|---|---|
| Borrar el pod de carga (A) | `kubectl delete pods -n pharmago -l app.kubernetes.io/name=pharmago-chaos --ignore-not-found` | 🟢 |
| Liberar la memoria del pod de la app (B) | `kubectl exec -n pharmago <pod> -- rm -f /dev/shm/fill /tmp/fill` | 🟢 |
| Reiniciar el pod afectado | `kubectl delete pod <pod> -n pharmago` | 🟢 MTTR observado: 5.6–6.8 s |

**Validación:** Q7 vuelve a la línea base, no hay pods `pharmago-chaos-*`, `RESTARTS` no sigue subiendo y *Memoria usada %* de `pharmago-m03` vuelve a su valor previo.

**Severidad típica:** SEV3. Pasa a SEV2 si hay OOMKilled en pods de la app que generen 5xx.

---

### 6.4 `storage` — llenado de almacenamiento

**Inyección:** A: pod `pharmago-chaos-storage-*` que escribe 128 MiB en un `emptyDir` de disco (`sizeLimit` 144 Mi, límite de `ephemeral-storage` 160 Mi). B: `dd` de 300 MB a `/tmp/chaos_fill` dentro de un pod `pharmago-api-gateway-*`, que **no tiene límite de `ephemeral-storage`**.

**Síntoma**

- Alerta: `Espacio de disco bajo` (< 15 % libre en `/` o `/data`, `for: 2m`). Con 128 MiB solo salta si el disco ya estaba casi lleno.
- Grafana, *PharmaGo - Infra*: el pod aparece en *Storage efímero por pod (pharmago)* y baja la serie de `pharmago-m03` en *Disco disponible %*. El Collector toma esta métrica cada 60 s: un pod de 20 s puede no aparecer.

**Diagnóstico**

1. K3 → `pharmago-chaos-storage-*`.
2. Q8: almacenamiento efímero por pod. Q11: disco disponible por nodo.
3. Variante B: `kubectl exec -n pharmago <pod> -- du -sh /tmp` y `kubectl exec -n pharmago <pod> -- ls -la /tmp`.
4. Disco del nodo: `minikube ssh -n pharmago-m03 -- df -h /`.

**Qué hace el sistema solo**

- A: si el pod supera su `sizeLimit` o su límite de `ephemeral-storage`, el kubelet lo desaloja (Evicted). Se borra al terminar `--duration`. Observado: **pico de 128.06 MiB**.
- B: **nada**. El archivo queda en el contenedor hasta que el pod se reinicia.

**Mitigación manual**

| Acción | Comando | Riesgo |
|---|---|---|
| Borrar el pod de carga (A) | `kubectl delete pods -n pharmago -l app.kubernetes.io/name=pharmago-chaos --ignore-not-found` | 🟢 |
| Borrar el archivo (B) | `kubectl exec -n pharmago <pod> -- rm -f /tmp/chaos_fill` | 🟢 |
| Reiniciar el pod afectado | `kubectl delete pod <pod> -n pharmago` | 🟢 |
| Limpiar imágenes o volúmenes del nodo | `minikube ssh -n <nodo> -- docker system prune` | 🔴 Las imágenes de la app usan `imagePullPolicy: Never`: si se borran, no hay de dónde volver a bajarlas. |
| Liberar espacio en `pharmago-m02` (ops) | — | 🔴 **Nunca** borrar `/mnt/data` ni los PV (SQL Server, Prometheus, Elasticsearch, Grafana) sin un procedimiento aprobado: no hay réplica de esos datos. |

**Validación:** Q8 sin el pod de carga y con el archivo borrado, *Disco disponible %* vuelve a su valor previo, la alerta vuelve a **Normal** y K3 queda vacío.

**Severidad típica:** SEV3. Pasa a SEV2 si se desaloja un pod de la app o el nodo queda por debajo del 15 % libre.

---

### 6.5 `network` — corte de red hacia pharmacy

**Inyección (A):** cambia el `targetPort` del Service `pharmago-pharmacy-service` de `80` a `1` durante `--duration`. Los pods siguen `Ready` (las probes van directo a la IP del pod) y las conexiones **nuevas** al Service se rechazan.

**Síntoma**

- Alerta: `[[ALERTA PENDIENTE: tasa de 5xx]]`. `Errores de red de nodo` **no** salta: no hay errores de la placa de red, el puerto está mal.
- Si el gateway abre conexiones nuevas: en Overview suben *Tasa de Error por Endpoint (%)* en las rutas de pharmacy y la serie `server_error` de *Tasa de Error por Tipo de Error*.
- Kibana: `kubernetes.labels.app : "pharmago-api-gateway" and Message : "Connection refused"`, seguido de `Circuit breaker ABIERTO para pharmacy-cluster`.
- Las conexiones HTTP persistentes del gateway pueden seguir funcionando durante el corte. **Puede no haber síntoma en Grafana**: confirmar con K8 contra el Service. `[[VERIFICAR]]` si en un corte de 20 s el gateway muestra errores.

**Diagnóstico**

1. `kubectl get svc pharmago-pharmacy-service -n pharmago -o jsonpath='{.spec.ports[0].targetPort}'` → tiene que dar **80**.
2. `kubectl get endpoints pharmago-pharmacy-service -n pharmago` → las IP tienen que estar en el puerto `:80` (con el corte aparecen en `:1`).
3. K6: muestra la diferencia del Service respecto del manifiesto.
4. K8 apuntando a `http://pharmago-pharmacy-service/health` (directo) y a `http://pharmago-api-gateway/api/drug` (por el gateway).
5. Descartar otras causas: `kubectl get networkpolicy -n pharmago` (el repo no define ninguna: cualquiera que aparezca es inyectada). En K2, pharmacy tiene que estar `2/2` (si no, es el escenario `component`).

**Qué hace el sistema solo**

- El gateway reintenta 2 veces (`GET`/`HEAD`/`OPTIONS`) con backoff de 100 y 200 ms más un jitter de hasta 100 ms. Con 5 fallos seguidos abre el circuit breaker y responde `503` al instante durante 5 s. Después deja pasar 1 request de prueba cada 5 s.
- **No corrige el Service.** Lo restaura el bloque `finally` del script al terminar `--duration`.
- Observado (2026-11-03): corte 22:19:24 → 22:19:48 UTC: **20 de 20 sondas fallidas**. Tras restaurar, **12 de 12 `200` inmediatas**.

**Mitigación manual**

| Acción | Comando | Riesgo |
|---|---|---|
| Restaurar el puerto | `kubectl patch svc pharmago-pharmacy-service -n pharmago --type=json -p='[{"op":"replace","path":"/spec/ports/0/targetPort","value":80}]'` | 🟢 Vuelve al valor del manifiesto. Si el script sigue corriendo, su restauración va a fallar (la operación `test` espera `1`): no afecta al servicio. |
| Alternativa: volver a aplicar el manifiesto | `kubectl apply -f services/backend/pharmacy-service-service.yaml` | 🟢 |
| Borrar una NetworkPolicy inyectada | `kubectl delete networkpolicy <nombre> -n pharmago` | 🟠 Confirmar antes con el IC que no es nuestra. |

**Validación:** el `targetPort` vuelve a `80`, `endpoints` muestra el puerto `:80`, K8 devuelve `200` por las dos rutas, Q1 = 0 y Kibana muestra `Circuit breaker CERRADO para pharmacy-cluster` (si se había abierto).

**Severidad típica:** SEV1 si fallan las compras por el gateway (Q1 > 25 % o K8 `/api/drug` ≠ 200). SEV2 si solo falla el acceso directo al Service.

---

### 6.6 `component` — desconexión de un componente

**Inyección (A):** escala `pharmago-pharmacy-service` de 2 a **0 réplicas** durante `--duration` y después restaura la cantidad original. Variantes posibles: borrar pods, matar el proceso, o bajar `pharmago-users-service` o `pharmago-db`.

**Síntoma**

- Alerta: `[[ALERTA PENDIENTE: tasa de 5xx]]`.
- Grafana, *PharmaGo - Overview*: *Tasa de Error por Endpoint (%)* sube en `/api/drug`, `/api/pharmacy` y `/api/purchases`, y aparece la serie `server_error` en *Tasa de Error por Tipo de Error*.
- Kibana: `kubernetes.labels.app : "pharmago-api-gateway" and Message : "Circuit breaker ABIERTO"`.

**Diagnóstico**

1. K2: ¿qué deployment no está `2/2`? `0/0` = lo escalaron a 0. `0/2` o `1/2` = los pods están cayendo o reiniciando.
2. K1 y K4: ¿pods en `CrashLoopBackOff`, `Terminating` o `Pending`?
3. Q3: ¿qué servicio devuelve 5xx? Si users **y** pharmacy devuelven `503`, sospechar de la base.
4. Base: `kubectl get pods -n pharmago -l app=pharmago-db` y, en Kibana, `"Database unavailable"`.
5. K6: ¿cambió `replicas` u otro campo respecto del manifiesto?

**Qué hace el sistema solo**

- **Escalado a 0:** Kubernetes respeta el valor nuevo y **no** lo revierte. El circuit breaker evita esperas: `502` en los primeros requests y después `503` inmediato con `X-Circuit-Breaker: open`. Lo restaura el script al terminar `--duration`.
- **Un pod borrado o caído:** la otra réplica atiende y el Deployment crea un pod nuevo. MTTR observado: **6.8 s** (pharmacy, borrado), **5.6 s** (users, borrado), **~10.7 s** (proceso muerto; hasta 18.9 s con el backoff de `CrashLoopBackOff`). Limitación conocida: puede fallar 1 request en el primer segundo.
- **Base caída:** users y pharmacy responden `503 Database unavailable` y el circuito se abre para los dos clusters. El readiness no chequea la base a propósito.
- Observado (2026-11-03): 2 → 0 réplicas a las 22:20:13 UTC: **17 de 17 fallidas** (2 × `502` y después `503`). Restauración a las 22:20:44: **11 de 11 `200`** a partir de las 22:20:56 (~12 s, rollout incluido). En la prueba del circuit breaker hubo unos 5 s de `503` después de restaurar, hasta que el circuito volvió a probar.

**Mitigación manual**

| Acción | Comando | Riesgo |
|---|---|---|
| Restaurar réplicas | `kubectl scale deployment/pharmago-pharmacy-service -n pharmago --replicas=2` y después `kubectl rollout status deployment/pharmago-pharmacy-service -n pharmago --timeout=180s` | 🟢 |
| Ídem para users | `kubectl scale deployment/pharmago-users-service -n pharmago --replicas=2` | 🟢 |
| Volver al manifiesto | `kubectl apply -f deployments/backend/pharmacy-service-deployment.yaml` | 🟢 |
| Reiniciar pods trabados | `kubectl rollout restart deployment/<deployment> -n pharmago` | 🟢 |
| Rollback de un cambio de configuración | `kubectl rollout undo deployment/<deployment> -n pharmago` (ver antes `kubectl rollout history deployment/<deployment> -n pharmago`) | 🟠 Solo revierte el pod template (env, probes, límites). **No revierte el código**: las imágenes son `:latest` con `imagePullPolicy: Never`. |
| Base escalada a 0 | `kubectl scale deployment/pharmago-db -n pharmago --replicas=1` | 🟠 Hay corte total hasta que pase la readiness (initialDelay de 30 s). Los datos persisten en el PV. |
| Reiniciar la base | `kubectl rollout restart deployment/pharmago-db -n pharmago` | 🔴 Réplica única: corte total. Solo si está colgada y lo decide el IC. |

**Validación:** K2 muestra todo `2/2` (base `1/1`), K8 contra `/api/drug` y `/test-health` devuelve `200`, Q1 = 0, Kibana muestra `Circuit breaker CERRADO para pharmacy-cluster` y no hay reinicios nuevos en K1.

**Severidad típica:** SEV1 (pharmacy en 0 = sin compras; users en 0 = sin login; base caída = todo). Un solo pod caído con la otra réplica atendiendo es SEV3.

---

## 7. Plantilla de registro de incidente

Copiar una por incidente. La llena el escriba **mientras ocurre**. Horas en local (UTC-3).

```
INCIDENTE #___                         Escenario sospechado: ___________
Registra: ________   IC: ________   Responder: ________

T_inicio (inyección, según docentes o log del script UTC-3h): __:__:__
T_detección (alerta o panel que lo muestra):                  __:__:__   Fuente: ____________
T_reconocimiento (IC declara SEV):                            __:__:__
T_mitigación (primera acción manual, si hubo):                __:__:__
T_restauración (validación OK):                               __:__:__
T_cierre:                                                     __:__:__

Descripción (1 línea): ____________________________________________
Servicios afectados: [ ] gateway [ ] users [ ] pharmacy [ ] base [ ] UI [ ] solo infra (nodo ______)
Impacto al usuario:  [ ] login  [ ] compras  [ ] catálogo  [ ] ninguno visible
Severidad inicial: SEV_   Severidad final: SEV_   Motivo del cambio: ____________

Acciones (una por línea; quién; comando o decisión; resultado)
hh:mm:ss | rol | acción | resultado
__:__:__ |     |        |
__:__:__ |     |        |
__:__:__ |     |        |

Mecanismo automático que actuó: [ ] probes/reinicio [ ] réplicas [ ] circuit breaker
                                [ ] reintentos [ ] rate limiting [ ] 503 por base [ ] ninguno

Métricas
MTTD = T_detección − T_inicio             = ____ s
MTTA = T_reconocimiento − T_detección     = ____ s
MTTR = T_restauración − T_inicio          = ____ s
Tasa de errores máx. (Q1)                 = ____ %
Disponibilidad durante el incidente       = ____ %  (requests OK / total, p. ej. del tráfico de fondo)
```

---

## 8. Plantilla de postmortem blameless

Se habla de sistemas y procesos, no de personas ("la alerta no saltó porque…", no "X no vio…").

```
# Postmortem — Incidente #___ (<fecha>)   SEV_   Autor: ______   Revisado por: ______

## Resumen
2–3 líneas: qué pasó, cuánto duró y a quién afectó.

## Impacto
- Usuarios / funcionalidades afectadas:
- Duración del impacto (T_inicio → T_restauración):
- Requests fallidos / tasa de 5xx máxima (Q1):
- MTTD / MTTA / MTTR:

## Línea de tiempo (UTC-3)
| Hora | Evento |
|---|---|
|  | Inicio |
|  | Detección (fuente) |
|  | SEV declarada |
|  | Mitigación |
|  | Restauración validada |

## Causa raíz
Qué la causó y por qué el sistema no la absorbió del todo (técnica de los 5 porqués).

## Qué funcionó
(ej. el circuit breaker respondió 503 al instante; el rolling restart no cortó el servicio)

## Qué no funcionó / dónde tuvimos suerte
(ej. la alerta no saltó por `for: 2m`; tardamos en encontrar el panel)

## Acciones correctivas
| # | Acción | Tipo (prevenir / detectar / mitigar) | Responsable | Fecha |
|---|---|---|---|---|
| 1 |  |  |  |  |
```

---

## 9. Checklist para el día de la defensa

### 9.1 Roles (completar antes)

| Rol | Persona |
|---|---|
| Incident Commander | `________` |
| Responder | `________` |
| Comunicaciones / escriba | `________` |

### 9.2 Qué tener abierto

- [ ] Terminal 1 (Git Bash, en `Implementacion K8S/Codigo/k8s`): `./port-forward.sh`.
- [ ] Terminal 2 (Responder): `kubectl get pods -n pharmago -o wide -w`.
- [ ] Terminal 3 (Responder): libre para diagnóstico y mitigación.
- [ ] Grafana http://127.0.0.1:3000 (admin/admin): *PharmaGo - Overview* y *PharmaGo - Infra*, rango `Last 15 minutes`, refresh `5s`.
- [ ] Grafana → Alerting → Alert rules → carpeta **PharmaGo** (5 reglas, más las 2 pendientes cuando estén).
- [ ] Prometheus http://127.0.0.1:9090 → Graph, con Q1, Q3 y Q6 cargadas en pestañas.
- [ ] Kibana http://127.0.0.1:5601 → Discover, data view `pharmago-logs-*`, filtro `kubernetes.labels.app : "pharmago-api-gateway"`, últimos 15 min, auto-refresh.
- [ ] Jaeger http://127.0.0.1:16686 (opcional): servicio `PharmaGo.ApiGateway`, para ver en qué tramo se corta una llamada en los escenarios `network` y `component`.
- [ ] Este documento y una copia en blanco de la [plantilla de registro](#7-plantilla-de-registro-de-incidente).
- [ ] Tráfico de fondo, para que `network` y `component` se vean en Grafana:

```bash
kubectl run war-room-traffic -n pharmago --image=busybox:1.36 --image-pull-policy=IfNotPresent \
  --restart=Never --overrides='{"spec":{"nodeSelector":{"node-type":"ops"}}}' -- sh -c '
i=0; while true; do
  case $((i % 3)) in 0) p=/api/drug ;; 1) p=/api/pharmacy ;; 2) p=/test-health ;; esac
  wget -q -T 3 -O /dev/null "http://pharmago-api-gateway$p" && r=ok || r=FAIL
  echo "$(date +%H:%M:%S) $r $p"; i=$((i + 1)); sleep 4
done'
# Ver resultados: kubectl logs -n pharmago war-room-traffic --tail=20
# Al terminar:    kubectl delete pod war-room-traffic -n pharmago
```

Son 15 req/min: no llegan al rate limiting. Replica el tráfico de la [línea base](evidencia/00-linea-base/06-metricas-linea-base.txt).

### 9.3 Cluster sano antes de arrancar

| Chequeo | Comando | Esperado |
|---|---|---|
| Nodos | `kubectl get nodes -L node-type` | 5 `Ready`; `pharmago-m02` = `ops`; `pharmago`, `pharmago-m03` y `pharmago-m04` = `backend` |
| Deployments | K2 | gateway, users y pharmacy `2/2`; `pharmago-db` y `pharmago-ui` `1/1` |
| Ubicación | `kubectl get pods -n pharmago -l 'workload-class=pharmago-backend-microservice' -o wide` | Cada servicio en su nodo (users `pharmago`, gateway `pharmago-m03`, pharmacy `pharmago-m04`) |
| Reinicios | K1 | `RESTARTS` estable (anotar el valor inicial) |
| Sin restos de caos | K3 | `No resources found` |
| Service de pharmacy | `kubectl get svc pharmago-pharmacy-service -n pharmago -o jsonpath='{.spec.ports[0].targetPort}'` | `80` |
| Sin cambios respecto del repo | K6 | Sin diferencias (o solo anotaciones de `rollout restart`) |
| Ruta de punta a punta | K8 con `/api/drug` y `/test-health` | `HTTP/1.1 200 OK` |
| Errores | Q1 | 0 |
| Alertas | Grafana → Alert rules | Las 5 en **Normal** |
| Etiqueta `service_name` en Prometheus | `count by (service_name) (pharmago_http_requests_total)` | `PharmaGo.ApiGateway`, `PharmaGo.UsersService` y `PharmaGo.PharmacyService`. `[[VERIFICAR]]` Si no aparece, usar `job` en vez de `service_name` en Q1–Q5. |
| Logs llegando | Kibana | Eventos del gateway de los últimos 2 min |

---

## 10. Referencia rápida: consultas y comandos

### PromQL (Prometheus, o Grafana → Explore)

Se usa `[1m]` para reaccionar rápido. Los paneles usan `[5m]` y **suavizan** los picos cortos: 30 s con 100 % de errores aparecen como ~10 % en el panel.

| ID | Qué mide | Consulta |
|---|---|---|
| Q1 | Tasa de 5xx del gateway (%), sin probes | `100 * sum(rate(pharmago_http_requests_total{service_name="PharmaGo.ApiGateway",status_code=~"5..",endpoint!~"/health\|/metrics"}[1m])) / sum(rate(pharmago_http_requests_total{service_name="PharmaGo.ApiGateway",endpoint!~"/health\|/metrics"}[1m]))` |
| Q2 | Req/s por endpoint en el gateway | `sum by (endpoint) (rate(pharmago_http_requests_total{service_name="PharmaGo.ApiGateway"}[1m]))` |
| Q3 | Códigos HTTP por servicio | `sum by (service_name, status_code) (rate(pharmago_http_requests_total[1m]))` |
| Q4 | 429 por endpoint | `sum by (endpoint) (rate(pharmago_http_requests_total{status_code="429"}[1m]))` |
| Q5 | Latencia promedio en el gateway (ms) | `sum by (endpoint) (rate(pharmago_http_request_duration_milliseconds_sum{service_name="PharmaGo.ApiGateway"}[1m])) / sum by (endpoint) (rate(pharmago_http_request_duration_milliseconds_count{service_name="PharmaGo.ApiGateway"}[1m]))` |
| Q6 | CPU por pod (núcleos) | `sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="pharmago",container!="",container!="POD"}[1m]))` |
| Q7 | Memoria por pod (MiB) | `sum by (pod) (container_memory_working_set_bytes{namespace="pharmago",container!="",container!="POD"}) / 1048576` |
| Q8 | Storage efímero por pod (MiB) | `k8s_pod_filesystem_usage_bytes{k8s_namespace_name="pharmago"} / 1048576` |
| Q9 | CPU de nodo (%) — expresión de la alerta | `100 * (1 - avg by (node) (rate(node_cpu_seconds_total{mode="idle"}[5m])))` |
| Q10 | Memoria de nodo (%) — expresión de la alerta | `100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)` |
| Q11 | Disco disponible (%) — expresión de la alerta | `min by (node) (100 * node_filesystem_avail_bytes{mountpoint=~"/\|/data",fstype!~"tmpfs\|overlay\|squashfs"} / node_filesystem_size_bytes{mountpoint=~"/\|/data",fstype!~"tmpfs\|overlay\|squashfs"})` |

(En el archivo crudo, los `|` de las expresiones regulares aparecen escapados como `\|` para no romper la tabla. En GitHub se ven bien. Si se copia desde el `.md`, sacar la barra invertida.)

### KQL (Kibana, data view `pharmago-logs-*`)

| Busca | KQL |
|---|---|
| Circuit breaker | `kubernetes.labels.app : "pharmago-api-gateway" and Message : "Circuit breaker"` |
| Reintentos | `kubernetes.labels.app : "pharmago-api-gateway" and Message : "Reintento"` |
| Conexión rechazada | `kubernetes.labels.app : "pharmago-api-gateway" and Message : "Connection refused"` |
| Base caída | `"Database unavailable"` |
| Errores de pharmacy | `kubernetes.labels.app : "pharmago-pharmacy-service" and LogLevel : "Error"` |
| Errores de users | `kubernetes.labels.app : "pharmago-users-service" and LogLevel : "Error"` |

`[[VERIFICAR]]` que `Message` esté indexado como texto (match por frase). Si es `keyword`, buscar el texto libre sin campo, por ejemplo `"Circuit breaker ABIERTO"`.

### kubectl

| ID | Para qué | Comando |
|---|---|---|
| K1 | Pods, nodo y reinicios | `kubectl get pods -n pharmago -o wide` |
| K2 | Réplicas por deployment | `kubectl get deploy -n pharmago` |
| K3 | Pods de caos | `kubectl get pods -n pharmago -l app.kubernetes.io/name=pharmago-chaos -o wide --show-labels` |
| K4 | Últimos eventos | `kubectl get events -n pharmago --sort-by=.lastTimestamp \| tail -20` |
| K5 | Logs del gateway | `kubectl logs -n pharmago -l app=pharmago-api-gateway --tail=50 --prefix` |
| K6 | Diferencias respecto del repo | `kubectl diff -f services/backend/ -f deployments/backend/` |
| K7 | Consumo en vivo | `kubectl top pods -n pharmago` `[[VERIFICAR]]` (metrics-server) |
| K8 | Prueba desde dentro del cluster | `kubectl run war-room-check -n pharmago --rm -i --restart=Never --image=busybox:1.36 --image-pull-policy=IfNotPresent -- wget -S -q -T 3 -O /dev/null http://pharmago-api-gateway/api/drug` (cambiar la URL según el caso) |

Restauración completa después de un `run-chaos.py` interrumpido (de [chaos-engineering/README.md](../Implementacion%20K8S/Codigo/chaos-engineering/README.md#restauración)):

```bash
kubectl patch svc pharmago-pharmacy-service -n pharmago --type=json \
  -p='[{"op":"replace","path":"/spec/ports/0/targetPort","value":80}]'
kubectl scale deployment/pharmago-pharmacy-service -n pharmago --replicas=2
kubectl rollout status deployment/pharmago-pharmacy-service -n pharmago --timeout=180s
kubectl delete pods -n pharmago -l app.kubernetes.io/name=pharmago-chaos --ignore-not-found
```

---

Fuentes: [run-chaos.py](../Implementacion%20K8S/Codigo/chaos-engineering/run-chaos.py), [grafana-alerts.yaml](../Implementacion%20K8S/Codigo/k8s/configmaps/grafana-alerts.yaml), [README-k8s.md](../Implementacion%20K8S/Codigo/k8s/README-k8s.md), [README-telemetry.md](../Implementacion%20K8S/Codigo/k8s/README-telemetry.md), evidencia en [02-alta-disponibilidad](evidencia/02-alta-disponibilidad/) y [07-scripts-caos](evidencia/07-scripts-caos/).
