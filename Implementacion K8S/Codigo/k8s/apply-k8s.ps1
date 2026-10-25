# Script de despliegue para Kubernetes (Minikube)
# Uso: .\apply-k8s.ps1

Write-Host "=== Desplegando PharmaGo en Kubernetes ===" -ForegroundColor Green

# Verificar que kubectl está disponible
if (-not (Get-Command kubectl -ErrorAction SilentlyContinue)) {
    Write-Host "Error: kubectl no está instalado o no está en el PATH" -ForegroundColor Red
    exit 1
}

# Verificar que el cluster Kubernetes está accesible
$nodes = kubectl get nodes --request-timeout=10s -o jsonpath='{.items[*].metadata.name}' 2>&1
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrEmpty($nodes)) {
    Write-Host "Error: kubectl no puede acceder a un cluster Kubernetes. Verifica el contexto actual con: kubectl config current-context" -ForegroundColor Red
    exit 1
}

$backendReady = kubectl get nodes -l node-type=backend -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}'
$opsReady = kubectl get nodes -l node-type=ops -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}'
$backendCount = @($backendReady -split '\s+' | Where-Object { $_ -eq 'True' }).Count
$opsCount = @($opsReady -split '\s+' | Where-Object { $_ -eq 'True' }).Count
if ($backendCount -lt 3 -or $opsCount -ne 1) {
    Write-Host "Error: Se requieren al menos 3 nodos Ready con node-type=backend y exactamente 1 con node-type=ops." -ForegroundColor Red
    Write-Host "Configura las etiquetas siguiendo README-k8s.md antes de desplegar." -ForegroundColor Yellow
    exit 1
}

Write-Host "`nPools de nodos configurados:" -ForegroundColor Cyan
kubectl get nodes -L node-type

Write-Host "`n2. Creando namespace..." -ForegroundColor Yellow
kubectl apply -f namespace.yaml
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n3. Creando secrets..." -ForegroundColor Yellow
kubectl apply -f secrets\db-secret.yaml
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n4. Creando configmaps..." -ForegroundColor Yellow
kubectl apply -f configmaps\prometheus-config.yaml
kubectl apply -f configmaps\otel-collector-config.yaml
kubectl apply -f configmaps\grafana-provisioning.yaml
kubectl apply -f configmaps\grafana-dashboards.yaml
kubectl apply -f configmaps\grafana-dashboard-infra.yaml
kubectl apply -f configmaps\grafana-alerts.yaml
kubectl apply -f configmaps\fluent-bit-config.yaml
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n5. Creando StorageClass y PersistentVolumes..." -ForegroundColor Yellow
kubectl apply -f persistent-volumes\storage-class.yaml
if ($LASTEXITCODE -ne 0) { exit 1 }

# Eliminar PVs existentes en estado Released para recrearlos
Write-Host "   Limpiando PVs existentes..." -ForegroundColor Cyan
kubectl delete pv sql-pv elasticsearch-pv prometheus-pv grafana-pv --ignore-not-found=true
Start-Sleep -Seconds 2

kubectl apply -f persistent-volumes\sql-pv.yaml
kubectl apply -f persistent-volumes\elasticsearch-pv.yaml
kubectl apply -f persistent-volumes\prometheus-pv.yaml
kubectl apply -f persistent-volumes\grafana-pv.yaml
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n6. Desplegando base de datos..." -ForegroundColor Yellow
kubectl apply -f services\ops\db-service.yaml
kubectl apply -f deployments\ops\db-deployment.yaml
if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n   Esperando a que la base de datos esté lista..." -ForegroundColor Yellow
# Esperar a que el pod esté Ready
$timeout = 0
$maxTimeout = 300
do {
    $podReady = kubectl get pod -l app=pharmago-db -n pharmago -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>&1
    if ($podReady -eq "True") {
        Write-Host "   Base de datos lista!" -ForegroundColor Green
        break
    }
    Start-Sleep -Seconds 5
    $timeout += 5
    if ($timeout -ge $maxTimeout) {
        Write-Host "   Timeout esperando la base de datos. Continuando..." -ForegroundColor Yellow
        break
    }
    Write-Host "   Esperando... ($timeout/$maxTimeout segundos)" -ForegroundColor Cyan
} while ($true)

Write-Host "`n7. Desplegando servicios de observabilidad..." -ForegroundColor Yellow
# Elasticsearch primero
kubectl apply -f services\ops\elasticsearch-service.yaml
kubectl apply -f deployments\ops\elasticsearch-deployment.yaml

# Esperar a que Elasticsearch esté listo
Write-Host "   Esperando a que Elasticsearch esté listo..." -ForegroundColor Yellow
$timeout = 0
$maxTimeout = 300
do {
    $podReady = kubectl get pod -l app=elasticsearch -n pharmago -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>&1
    if ($podReady -eq "True") {
        Write-Host "   Elasticsearch listo!" -ForegroundColor Green
        break
    }
    Start-Sleep -Seconds 5
    $timeout += 5
    if ($timeout -ge $maxTimeout) {
        Write-Host "   Timeout esperando Elasticsearch. Continuando..." -ForegroundColor Yellow
        break
    }
    Write-Host "   Esperando... ($timeout/$maxTimeout segundos)" -ForegroundColor Cyan
} while ($true)

# Resto de servicios ops
kubectl apply -f services\ops\otel-collector-service.yaml
kubectl apply -f services\ops\jaeger-service.yaml
kubectl apply -f deployments\ops\jaeger-deployment.yaml
kubectl apply -f deployments\ops\prometheus-serviceaccount.yaml
kubectl apply -f deployments\ops\prometheus-clusterrole.yaml
kubectl apply -f deployments\ops\prometheus-clusterrolebinding.yaml
kubectl apply -f services\ops\prometheus-service.yaml
kubectl apply -f deployments\ops\prometheus-deployment.yaml
kubectl apply -f deployments\ops\otel-collector-serviceaccount.yaml
kubectl apply -f deployments\ops\otel-collector-clusterrole.yaml
kubectl apply -f deployments\ops\otel-collector-clusterrolebinding.yaml
kubectl apply -f deployments\ops\otel-collector-deployment.yaml

kubectl apply -f services\ops\node-exporter-service.yaml
kubectl apply -f deployments\ops\node-exporter-daemonset.yaml

kubectl apply -f services\ops\grafana-service.yaml
kubectl apply -f deployments\ops\grafana-deployment.yaml

kubectl apply -f services\ops\kibana-service.yaml
kubectl apply -f deployments\ops\kibana-deployment.yaml

kubectl apply -f deployments\ops\fluent-bit-serviceaccount.yaml
kubectl apply -f deployments\ops\fluent-bit-clusterrole.yaml
kubectl apply -f deployments\ops\fluent-bit-clusterrolebinding.yaml
kubectl apply -f deployments\ops\fluent-bit-daemonset.yaml

if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n8. Desplegando servicios backend..." -ForegroundColor Yellow
kubectl apply -f services\backend\users-service-service.yaml
kubectl apply -f deployments\backend\users-service-deployment.yaml

kubectl apply -f services\backend\pharmacy-service-service.yaml
kubectl apply -f deployments\backend\pharmacy-service-deployment.yaml

kubectl apply -f services\backend\api-gateway-service.yaml
kubectl apply -f deployments\backend\api-gateway-deployment.yaml

if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n9. Desplegando frontend..." -ForegroundColor Yellow
kubectl apply -f services\frontend\ui-service.yaml
kubectl apply -f deployments\frontend\ui-deployment.yaml

if ($LASTEXITCODE -ne 0) { exit 1 }

Write-Host "`n=== Despliegue completado ===" -ForegroundColor Green
Write-Host "`nVerificando estado de los pods..." -ForegroundColor Yellow
kubectl get pods -n pharmago -o wide

Write-Host "`nPara ver los servicios expuestos:" -ForegroundColor Cyan
Write-Host "  Frontend:     minikube service pharmago-ui -n pharmago --url" -ForegroundColor White
Write-Host "  Grafana:      minikube service grafana -n pharmago --url" -ForegroundColor White
Write-Host "  Kibana:       minikube service kibana -n pharmago --url" -ForegroundColor White
Write-Host "  Prometheus:   minikube service prometheus -n pharmago --url" -ForegroundColor White

