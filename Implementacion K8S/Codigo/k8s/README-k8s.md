# Despliegue de PharmaGo en Kubernetes

Esta guía despliega PharmaGo en un cluster multinodo. Para el primer requisito del proyecto, los tres microservicios backend deben quedar en nodos Kubernetes distintos.

## Topología requerida

El cluster tiene 5 nodos. Esta es la asignación de referencia del equipo, la misma en todos los clusters (ver también [deployment-diagram.md](deployment-diagram.md)):

| Nodo | Rol (`node-type`) | Qué corre |
|---|---|---|
| `pharmago` (control-plane) | `backend` | users-service ×2 |
| `pharmago-m02` | `ops` | Base de datos, OTel Collector, Prometheus, Grafana, Elasticsearch y Kibana, con sus PersistentVolumes |
| `pharmago-m03` | `backend` | api-gateway ×2 y UI |
| `pharmago-m04` | `backend` | pharmacy-service ×2 |
| `pharmago-m05` | sin rol | Solo los DaemonSets (node-exporter y fluent-bit) |

Las reglas de los manifests:

- **3 nodos `backend`**: uno por microservicio (`api-gateway`, `users-service`, `pharmacy-service`). Cada microservicio corre 2 réplicas y las dos van juntas a su nodo. La anti-afinidad obligatoria impide que Pods de microservicios distintos compartan el mismo hostname Kubernetes.
- **1 nodo `ops`**: base de datos, componentes de observabilidad y los PersistentVolumes locales.
- **UI**: puede ejecutarse en cualquiera de los nodos `backend`; no cuenta como uno de los tres microservicios backend.
- **Node Exporter y Fluent Bit**: DaemonSets que se ejecutan por nodo para observar infraestructura y recoger logs.

Las tres Deployments backend se actualizan con `RollingUpdate` (`maxSurge: 1`, `maxUnavailable: 0`): primero se levanta un Pod nuevo en el nodo del servicio y recién cuando está `Ready` se baja uno viejo, así que el servicio nunca queda con menos de 2 réplicas. Ver [Despliegue sin caída](#despliegue-sin-caída).

Las dos réplicas de un servicio comparten nodo: cubren la falla de un Pod y los despliegues, no la caída de ese nodo. Repartirlas exigiría nodos backend extra, porque cada nodo aloja un solo microservicio.

Los nodos con rol deben estar `Ready`, ser schedulable y no tener taints `NoSchedule` / `NoExecute` no tolerados. Los manifests usan etiquetas de nodo y hostname, así que prueban separación entre **nodos Kubernetes**.

Las reglas garantizan que cada microservicio quede en un nodo `backend` distinto, pero en cuál de los tres cae cada uno lo decide el scheduler. Para que el cluster quede igual a la asignación de referencia, después del primer despliegue se corre `place-services.sh` (ver [Ubicar cada servicio en su nodo](#ubicar-cada-servicio-en-su-nodo)). Una vez ubicados, los Pods se mantienen en su nodo en los reinicios y en los rolling updates.

Los PV actuales son estáticos y usan `hostPath`, que guarda los datos en el disco local del nodo. Por eso la base de datos y los servicios que montan estos volúmenes se restringen al nodo `ops`, y ese nodo debe tener al menos 5–6 GB de memoria disponible. Sus manifiestos conservan la etiqueta heredada `all` para no requerir una modificación de volúmenes persistentes existentes; `configure-nodes.sh` quita esa etiqueta de los nodos antes de asignar los roles, así que solo `ops` coincide. Si reutilizas un cluster con datos, asigna como `ops` el mismo nodo que ya contiene los directorios `/mnt/data`; moverlos requiere migrar los datos. Esta topología sirve para el entorno docente/local; no aporta almacenamiento replicado ni alta disponibilidad.

## Prerrequisitos

1. **Minikube** instalado y configurado
2. **kubectl** instalado y configurado
3. **Docker** instalado (para construir las imágenes)

## Configuración inicial

### 1. Preparar el cluster

Usa un cluster de 5 nodos. Crea el perfil multinodo desde el inicio, en lugar de añadir nodos a un perfil de una sola máquina. Este comando crea un perfil separado y conserva cualquier perfil Minikube existente:

```bash
minikube start --profile pharmago --nodes=5
minikube profile pharmago
kubectl config use-context pharmago
kubectl get nodes -o wide
```

Antes de continuar, comprueba que el equipo/cluster tiene recursos suficientes. Reserva al menos 5–6 GB para el nodo `ops` por la base de datos y la pila de observabilidad; cada nodo backend también necesita recursos para su servicio y el agente de logs. Si los nodos son VMs, confirma que tienen recursos propios y que están `Ready`.

El comando anterior indica el número de nodos, no dimensiona la memoria para estos Pods. Configura Minikube y el host con recursos suficientes; el límite de memoria de Minikube puede aplicarse por nodo y elevar mucho el consumo total. Si el equipo no puede sostener la carga, usa un cluster de laboratorio o hosts/VMs con recursos separados.

### 2. Asignar roles a los nodos

Desde esta carpeta, con la asignación de referencia (en Windows, desde Git Bash):

```bash
chmod +x configure-nodes.sh
./configure-nodes.sh pharmago-m02 pharmago-m03 pharmago pharmago-m04
kubectl get nodes -L node-type
```

Los argumentos son, en orden, el nodo `ops` y los nodos `backend` de gateway, users y pharmacy. `pharmago-m05` queda sin rol. El script valida que los nodos existan, estén `Ready`, sean schedulable y no tengan taints que bloqueen los Pods. También reserva la etiqueta `node-type` para esta topología: quita los valores anteriores de todos los nodos para que una etiqueta vieja (`all`, por ejemplo) no altere la colocación.

### 3. Construir y cargar imágenes Docker

**IMPORTANTE**: Usa `minikube image load` para cargar las imágenes en Minikube (no `minikube docker-env`).

#### Opción 1: Script automatizado (Recomendado)

```bash
cd k8s
chmod +x build-images.sh
./build-images.sh
```

#### Opción 2: Manual

Desde el directorio `Implementacion K8S/Codigo`:

```bash
# Construir imágenes de backend
cd Backend
docker build -f PharmaGo.UsersService/Dockerfile -t pharmago-users-service:latest .
minikube image load pharmago-users-service:latest

docker build -f PharmaGo.PharmacyService/Dockerfile -t pharmago-pharmacy-service:latest .
minikube image load pharmago-pharmacy-service:latest

docker build -f PharmaGo.ApiGateway/Dockerfile -t pharmago-api-gateway:latest .
minikube image load pharmago-api-gateway:latest

# Construir imagen de frontend
cd ../Frontend
docker build -f Dockerfile -t pharmago-ui:latest .
minikube image load pharmago-ui:latest
```

**Nota**: `minikube image load` carga las imágenes en el entorno Minikube del cluster; confirma que cada Pod pueda encontrar su imagen antes del despliegue.

## Despliegue

**IMPORTANTE**: Asegúrate de haber construido y cargado las imágenes Docker primero (paso 3).

El script `apply-k8s.sh` (o `apply-k8s.ps1` en PowerShell) comprueba que el contexto de `kubectl` responde y que hay al menos tres nodos `backend` y exactamente un nodo `ops` en estado `Ready`. Si el preflight falla, corrige la topología con `configure-nodes.sh` antes de aplicar los manifests.

### Opción 1: Script automatizado (Recomendado)

```bash
cd k8s
chmod +x apply-k8s.sh
./apply-k8s.sh
```

### Opción 2: Despliegue manual

```bash
cd k8s

# 1. Namespace
kubectl apply -f namespace.yaml

# 2. Secrets
kubectl apply -f secrets/db-secret.yaml

# 3. ConfigMaps
kubectl apply -f configmaps/

# 4. PersistentVolumes
kubectl apply -f persistent-volumes/

# 5. Database
kubectl apply -f services/ops/db-service.yaml
kubectl apply -f deployments/ops/db-deployment.yaml
kubectl wait --for=condition=ready pod -l app=pharmago-db -n pharmago --timeout=300s

# 6. Ops services (Elasticsearch primero)
kubectl apply -f services/ops/elasticsearch-service.yaml
kubectl apply -f deployments/ops/elasticsearch-deployment.yaml
kubectl wait --for=condition=ready pod -l app=elasticsearch -n pharmago --timeout=300s

kubectl apply -f services/ops/
kubectl apply -f deployments/ops/

# 7. Backend services
kubectl apply -f services/backend/
kubectl apply -f deployments/backend/

# 8. Frontend
kubectl apply -f services/frontend/
kubectl apply -f deployments/frontend/
```

## Ubicar cada servicio en su nodo

Después del primer despliegue, `place-services.sh` deja cada microservicio en el nodo de la asignación de referencia: users en `pharmago`, gateway y UI en `pharmago-m03`, y pharmacy en `pharmago-m04`.

```bash
cd k8s
chmod +x place-services.sh
./place-services.sh
```

El script baja los tres microservicios y la UI a 0 réplicas y los vuelve a subir de a uno. Para que el scheduler elija el nodo correcto, bloquea temporalmente (`kubectl cordon`) los nodos que no corresponden, y al terminar los desbloquea aunque algo falle. Durante esos ~2 minutos la aplicación no atiende requests, así que se corre una sola vez, al montar el cluster. Después los Pods se mantienen en su nodo en los reinicios y en los rolling updates.

## Verificación

### Ver estado de los pods

```bash
# Ver todos los pods y el nodo Kubernetes asignado
kubectl get pods -n pharmago -o wide

# Ver roles y estado de los nodos
kubectl get nodes -L node-type

# Comprobar que los tres microservicios backend tienen nodos distintos
kubectl get pods -n pharmago -l 'workload-class=pharmago-backend-microservice' -o wide
```

En la salida del último comando, las 2 réplicas de cada microservicio comparten el valor de la columna `NODE` y cada microservicio aparece en un nodo distinto. Guarda esta salida como evidencia del requisito junto con el diagrama de despliegue de `deployment-diagram.md`.

## Despliegue sin caída

Cada microservicio backend se reemplaza sin cortar el servicio gracias a:

- **`RollingUpdate`** con `maxSurge: 1` y `maxUnavailable: 0`, más `minReadySeconds: 5`: siempre hay 2 réplicas listas mientras se reemplazan.
- **Probes `httpGet /health`**: el Service solo envía tráfico a Pods que responden.
- **`preStop` de 5 segundos**: Kubernetes saca al Pod del Service y le envía SIGTERM al mismo tiempo. La pausa deja que el Pod siga atendiendo hasta que kube-proxy deja de enrutarle requests.
- **Warm-up de pharmacy**: el servicio hace una consulta a la BD antes de empezar a escuchar, así que no queda `Ready` hasta poder responder con latencia normal. Sin esto, la primera consulta de cada Pod nuevo tardaba ~3s.

Para comprobarlo:

```bash
cd k8s
./test-zero-downtime.sh                          # reemplaza gateway, users y pharmacy
./test-zero-downtime.sh pharmago-users-service   # o solo los que indiques
```

El script lanza un Pod dentro del cluster que consulta el gateway sin parar (gateway solo, gateway → users y gateway → pharmacy → BD), reinicia los deployments y reporta los requests fallidos. Corre dentro del cluster porque `kubectl port-forward` se ata a un solo Pod y se corta cuando ese Pod se reemplaza. El resultado esperado es `Fallidos: 0`.

### Migrar un cluster con la versión anterior

Si el cluster tiene los deployments backend anteriores (anti-afinidad entre todos los backend y estrategia `Recreate`), los Pods viejos rechazan a los nuevos en su nodo y el rollout queda `Pending`. Hay que recrearlos una vez, con una interrupción breve:

```bash
kubectl delete -f deployments/backend/
kubectl wait --for=delete pod -l workload-class=pharmago-backend-microservice -n pharmago --timeout=120s
kubectl apply -f deployments/backend/
```

Esperar a que los Pods viejos desaparezcan antes del `apply` es necesario: si todavía existe uno, la afinidad obliga a los nuevos a ir a su nodo, y el scheduler puede tardar hasta 5 minutos en reintentar.

### Ver servicios

```bash
kubectl get svc -n pharmago
```

### Ver logs

```bash
# Logs de un pod específico
kubectl logs -n pharmago <pod-name>

# Logs con seguimiento
kubectl logs -n pharmago -f <pod-name>
```

## Acceso a los Servicios

Hay dos opciones para acceder a los servicios: **port-forward** (recomendado en Windows) o **minikube service**.

### Opción 1: Port-forward (Recomendado en Windows)

En Windows, `localhost` puede fallar con `kubectl port-forward` y herramientas como `sqlcmd`. Usa **127.0.0.1** para todas las conexiones.

```bash
cd k8s
./port-forward.sh
```

Servicios disponibles en:
- **Frontend**: http://127.0.0.1:4200
- **API Gateway**: http://127.0.0.1:5000
- **BD (SQL Server)**: `127.0.0.1,11433` (sa / Str0ngP@ssword!)
- **Prometheus**: http://127.0.0.1:9090
- **Grafana**: http://127.0.0.1:3000 (admin/admin)
- **Kibana**: http://127.0.0.1:5601

Para detener: `./port-forward.sh --stop`

### Opción 2: Minikube service

### Frontend

```bash
minikube service pharmago-ui -n pharmago --url
```

### Grafana

```bash
minikube service grafana -n pharmago --url
# Credenciales: admin / admin
```

### Kibana

```bash
minikube service kibana -n pharmago --url
```

### Prometheus

```bash
minikube service prometheus -n pharmago --url
```

## Configuración de Replicas

Para cambiar el número de réplicas de cualquier componente, edita el archivo de deployment correspondiente:

```yaml
spec:
  replicas: 1  # Cambiar este valor
```

Luego aplica los cambios:

```bash
kubectl apply -f deployments/<component>/<deployment>.yaml
```

Las réplicas de un microservicio backend van todas al mismo nodo (afinidad con su propio `app`), así que subirlas no requiere nodos nuevos, solo memoria disponible en ese nodo.

## Troubleshooting

### Pods no se inician (Pending / telemetría)

Si los pods de telemetría (otel-collector, prometheus, grafana, elasticsearch, kibana, fluent-bit) quedan en `Pending`:

1. **Falta un pool o no alcanza la capacidad**: se necesitan al menos tres nodos `backend` y uno `ops`. Ejecuta `kubectl get nodes -L node-type` y vuelve a asignar los roles con `configure-nodes.sh`.
2. **Un nodo no se puede programar**: comprueba que está `Ready`, no está cordoned y no tiene taints no tolerados. El Pod backend quedará `Pending` si no hay un hostname disponible distinto para él.
3. **Un Pod de estado queda `Pending`**: revisa que los PV estén disponibles y que `node-type=ops` siga puesto en el mismo nodo donde se crearon los directorios locales de `hostPath`.

4. Verificar eventos del pod:
   ```bash
   kubectl describe pod <pod-name> -n pharmago
   ```

5. Ver logs:
   ```bash
   kubectl logs <pod-name> -n pharmago
   ```

### Imágenes no encontradas

Si los pods fallan con `ImagePullBackOff`:

1. Verificar que las imágenes están cargadas en Minikube:
   ```bash
   minikube image ls | grep pharmago
   ```

2. Si faltan imágenes, reconstruirlas y cargarlas:
   ```bash
   ./build-images.sh
   ```

3. Verificar que los deployments usan `imagePullPolicy: Never` (ya configurado).

### El Pod sigue con el código anterior después de reconstruir

`minikube image load` no reemplaza una imagen `:latest` que un contenedor está usando en ese nodo. Si después de `build-images.sh` el Pod nuevo corre el código anterior (o un rollout no llega a `Ready`), cargá la imagen directamente en cada nodo. Con el driver docker, los nodos son contenedores del host:

```bash
for n in $(kubectl get nodes -o name | cut -d/ -f2); do
  docker save pharmago-api-gateway:latest | docker exec -i "$n" docker load
done
kubectl rollout restart deployment/pharmago-api-gateway -n pharmago
```

### Base de datos no está lista

Si los servicios backend no pueden conectarse a la base de datos:

1. Verificar que el pod de la base de datos está corriendo:
   ```bash
   kubectl get pods -n pharmago -l app=pharmago-db
   ```

2. Verificar logs de la base de datos:
   ```bash
   kubectl logs -n pharmago -l app=pharmago-db
   ```

3. Verificar el secret:
   ```bash
   kubectl get secret db-secret -n pharmago -o yaml
   ```

### PersistentVolumes no se montan

En Minikube, los PersistentVolumes usan `hostPath` con `DirectoryOrCreate`, que crea los directorios automáticamente. Si tienes problemas:

```bash
# Conectarse al nodo y crear directorios manualmente
minikube ssh
sudo mkdir -p /mnt/data/{sql,elasticsearch,prometheus,grafana}
sudo chmod 777 /mnt/data/{sql,elasticsearch,prometheus,grafana}
exit
```

**Nota**: El deployment de SQL Server incluye un initContainer que configura los permisos para el usuario `mssql` (UID 10001).

### Elasticsearch OOMKilled (Out of Memory)

Si Elasticsearch está en `CrashLoopBackOff` con `OOMKilled`:

1. **Verificar memoria disponible en el nodo:**
   ```bash
   kubectl describe node minikube | grep -A 5 "Allocated resources"
   ```

2. **Aumentar memoria de Minikube:**
   ```bash
   minikube stop
   minikube delete
   minikube start --memory=6144 --cpus=4
   ```

3. **Reducir memoria heap de Elasticsearch:**
   Edita `deployments/ops/elasticsearch-deployment.yaml` y reduce `ES_JAVA_OPTS`:
   ```yaml
   - name: ES_JAVA_OPTS
     value: "-Xms1g -Xmx1g"  # Reducir de 2g a 1g
   ```
   Luego aumenta el límite de memoria del contenedor a 3-4Gi para compensar.

4. **Deshabilitar características que consumen memoria:**
   Agrega estas variables de entorno en el deployment:
   ```yaml
   - name: xpack.monitoring.collection.enabled
     value: "false"
   - name: xpack.security.enabled
     value: "false"  # Ya está configurado
   ```

5. **Verificar logs:**
   ```bash
   kubectl logs -n pharmago -l app=elasticsearch --tail=50
   ```

**Nota**: Elasticsearch requiere al menos 2GB de heap de Java y memoria adicional para el sistema operativo y otros procesos. En un entorno con recursos limitados (Minikube), considera usar una versión más ligera o deshabilitar características no esenciales.

### Kibana no está Ready

Si Kibana está `Running` pero no `Ready`:

1. **Verificar que Elasticsearch está funcionando:**
   ```bash
   kubectl get pods -n pharmago -l app=elasticsearch
   ```

2. **Verificar logs de Kibana:**
   ```bash
   kubectl logs -n pharmago -l app=kibana --tail=50
   ```

3. **Kibana espera a Elasticsearch:** Si Elasticsearch no está listo, Kibana no puede conectarse y permanecerá en `Running` pero no `Ready`.

## Limpieza (Equivalente a `docker-compose down`)

### Opción 1: Script automatizado (Recomendado)

```bash
cd k8s
chmod +x cleanup.sh
./cleanup.sh
```

**Conservar volúmenes persistentes:**
```bash
./cleanup.sh --keep-volumes
```

### Opción 2: Comando manual

```bash
# Eliminar namespace (elimina todos los recursos dentro de él)
kubectl delete namespace pharmago

# Eliminar PersistentVolumes (están fuera del namespace)
kubectl delete pv sql-pv elasticsearch-pv prometheus-pv grafana-pv
```

### Opción 3: Eliminar recursos individualmente

```bash
# Eliminar recursos en orden inverso al despliegue
kubectl delete -f deployments/
kubectl delete -f services/
kubectl delete -f configmaps/
kubectl delete -f secrets/
kubectl delete -f persistent-volumes/
kubectl delete -f namespace.yaml
```

**Nota**: Eliminar el namespace es la forma más simple y equivalente a `docker-compose down`, ya que elimina todos los recursos (pods, services, deployments, configmaps, secrets, PVCs) de una vez. Los PersistentVolumes deben eliminarse por separado ya que están fuera del namespace.

## Notas Importantes

1. **Logs**: Fluent Bit (DaemonSet) recolecta los logs de los pods y los envía directamente a Elasticsearch con índice `pharmago-logs-*`. Kibana se usa para visualizarlos.

2. **PersistentVolumes**: Los PVs usan `hostPath` que es adecuado para desarrollo pero no para producción. En producción, usa storage classes apropiadas.

3. **Secrets**: La contraseña de la base de datos está en texto plano en el secret. Para producción, considera usar herramientas como Sealed Secrets o external secret managers.

4. **Health Checks**: Los deployments incluyen health checks básicos. Asegúrate de que tus aplicaciones expongan endpoints `/health` o ajusta las configuraciones según corresponda.

5. **Recursos**: Los límites de recursos están configurados de forma conservadora. Ajusta según las necesidades de tu entorno.

## Estructura de Archivos

```
k8s/
├── namespace.yaml
├── configmaps/
│   ├── prometheus-config.yaml
│   ├── otel-collector-config.yaml
│   ├── grafana-provisioning.yaml
│   └── fluent-bit-config.yaml
├── secrets/
│   └── db-secret.yaml
├── persistent-volumes/
│   ├── sql-pv.yaml
│   ├── elasticsearch-pv.yaml
│   ├── prometheus-pv.yaml
│   └── grafana-pv.yaml
├── deployments/
│   ├── frontend/
│   │   └── ui-deployment.yaml
│   ├── backend/
│   │   ├── api-gateway-deployment.yaml
│   │   ├── users-service-deployment.yaml
│   │   └── pharmacy-service-deployment.yaml
│   └── ops/
│       ├── db-deployment.yaml
│       ├── otel-collector-deployment.yaml
│       ├── prometheus-deployment.yaml
│       ├── grafana-deployment.yaml
│       ├── elasticsearch-deployment.yaml
│       ├── kibana-deployment.yaml
│       └── fluent-bit-daemonset.yaml
├── services/
│   ├── frontend/
│   │   └── ui-service.yaml
│   ├── backend/
│   │   ├── api-gateway-service.yaml
│   │   ├── users-service-service.yaml
│   │   └── pharmacy-service-service.yaml
│   └── ops/
│       ├── db-service.yaml
│       ├── otel-collector-service.yaml
│       ├── prometheus-service.yaml
│       ├── grafana-service.yaml
│       ├── elasticsearch-service.yaml
│       └── kibana-service.yaml
├── build-images.sh
├── configure-nodes.sh
├── apply-k8s.sh
├── place-services.sh
├── test-zero-downtime.sh
├── cleanup.sh
└── README-k8s.md
```

