# Topología de despliegue de PharmaGo en Kubernetes

Ubicación real de los Pods en el cluster `pharmago` (minikube, 5 nodos), observada el 21/10/2026 con `kubectl get pods -n pharmago -o wide`.

```mermaid
flowchart LR
    Client[Browser / cliente]
    UIService[Service pharmago-ui]
    GatewayService[Service pharmago-api-gateway]
    UsersService[Service pharmago-users-service]
    PharmacyService[Service pharmago-pharmacy-service]
    DBService[Service pharmago-db]

    subgraph N1["pharmago · 192.168.49.2 · control-plane · node-type=backend"]
        Users[users-service Pods ×2]
        NE1[node-exporter]
        FB1[fluent-bit]
    end
    subgraph N3["pharmago-m03 · 192.168.49.4 · node-type=backend"]
        Gateway[api-gateway Pods ×2]
        UI[pharmago-ui Pod]
        NE3[node-exporter]
        FB3[fluent-bit]
    end
    subgraph N4["pharmago-m04 · 192.168.49.5 · node-type=backend"]
        Pharmacy[pharmacy-service Pods ×2]
        NE4[node-exporter]
        FB4[fluent-bit]
    end
    subgraph N2["pharmago-m02 · 192.168.49.3 · node-type=ops"]
        DB[(SQL Server + PV local)]
        OTEL[OTel Collector]
        Prom[Prometheus + PV local]
        Grafana[Grafana + PV local]
        ES[Elasticsearch + PV local]
        Kibana[Kibana]
        NE2[node-exporter]
        FB2[fluent-bit]
    end
    subgraph N5["pharmago-m05 · 192.168.49.6 · sin rol (libre)"]
        NE5[node-exporter]
        FB5[fluent-bit]
    end

    Client --> UIService --> UI
    UI --> GatewayService --> Gateway
    Gateway --> UsersService --> Users
    Gateway --> PharmacyService --> Pharmacy
    Users <-.-> Pharmacy
    Users --> DBService --> DB
    Pharmacy --> DBService

    Gateway -. OTLP .-> OTEL
    Users -. OTLP .-> OTEL
    Pharmacy -. OTLP .-> OTEL
    OTEL -. scrape .-> Prom
    NE1 & NE2 & NE3 & NE4 & NE5 -. scrape .-> Prom
    Prom --> Grafana
    FB1 & FB2 & FB3 & FB4 & FB5 -. logs .-> ES
    ES --> Kibana
```

## Justificación

- **Un microservicio por nodo:** los tres Deployments de backend tienen una `podAntiAffinity` obligatoria por `kubernetes.io/hostname` que impide que dos microservicios distintos compartan nodo. Si no hay nodo válido, el Pod queda `Pending` en vez de violar la regla.
- **Dos réplicas juntas:** una `podAffinity` con su propio `app` mantiene las 2 réplicas de cada servicio en el mismo nodo. Cubren la falla de un Pod y los `RollingUpdate` sin caída, pero no la caída del nodo entero.
- **Nodo `ops`:** la base de datos y la observabilidad están fijadas con `nodeAffinity`, porque sus PersistentVolumes son `hostPath` (disco local del nodo). Así también quedan aisladas de la carga de negocio.
- **DaemonSets:** node-exporter y fluent-bit corren en todos los nodos para recolectar métricas y logs.
- **Alcance:** en qué nodo `backend` cae cada servicio lo decide el scheduler y puede cambiar entre despliegues. Lo que está garantizado es la separación. Los 5 nodos son contenedores de minikube sobre una sola máquina física: el despliegue demuestra separación entre nodos Kubernetes, no entre hosts físicos.
