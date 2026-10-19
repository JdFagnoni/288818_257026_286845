# PharmaGo Kubernetes deployment topology

This is the target placement represented by the manifests. Replace the generic node names with the names shown by `kubectl get nodes -L node-type` when preparing the project evidence.

```mermaid
flowchart LR
    Client[Browser / client]
    UIService[UI Service]
    GatewayService[API Gateway Service]
    UsersService[Users Service]
    PharmacyService[Pharmacy Service]
    DBService[Database Service]

    subgraph Backend1[Backend node 1 · node-type=backend]
        UI[pharmago-ui Pod]
        Gateway[API Gateway Pods ×2]
        NE1[Node Exporter DaemonSet Pod]
        FB1[Fluent Bit DaemonSet Pod]
    end
    subgraph Backend2[Backend node 2 · node-type=backend]
        Users[Users Service Pods ×2]
        NE2[Node Exporter DaemonSet Pod]
        FB2[Fluent Bit DaemonSet Pod]
    end
    subgraph Backend3[Backend node 3 · node-type=backend]
        Pharmacy[Pharmacy Service Pods ×2]
        NE3[Node Exporter DaemonSet Pod]
        FB3[Fluent Bit DaemonSet Pod]
    end
    subgraph Ops[Ops node · node-type=ops]
        DB[SQL Server Pod + local PV]
        OTEL[OpenTelemetry Collector Pod]
        Prom[Prometheus Pod + local PV]
        Grafana[Grafana Pod + local PV]
        ES[Elasticsearch Pod + local PV]
        Kibana[Kibana Pod]
        NE4[Node Exporter DaemonSet Pod]
        FB4[Fluent Bit DaemonSet Pod]
    end

    Client --> UIService --> UI
    UI --> GatewayService --> Gateway
    Gateway --> UsersService --> Users
    Gateway --> PharmacyService --> Pharmacy
    Users --> DBService --> DB
    Pharmacy --> DBService

    Gateway -. OTLP telemetry .-> OTEL
    Users -. OTLP telemetry .-> OTEL
    Pharmacy -. OTLP telemetry .-> OTEL
    OTEL -. Prometheus scrape endpoint .-> Prom
    NE1 -. scrape .-> Prom
    NE2 -. scrape .-> Prom
    NE3 -. scrape .-> Prom
    NE4 -. scrape .-> Prom
    Prom --> Grafana
    FB1 -. logs .-> ES
    FB2 -. logs .-> ES
    FB3 -. logs .-> ES
    FB4 -. logs .-> ES
    ES --> Kibana
```

A required Pod anti-affinity rule keeps different backend microservices on different `kubernetes.io/hostname` values, and a required Pod affinity keeps the two replicas of each microservice together on its node. The diagram shows Kubernetes node separation. Multiple Minikube nodes on one laptop are still backed by one physical machine; use separate physical hosts if the grading requirement demands physical-machine separation.
