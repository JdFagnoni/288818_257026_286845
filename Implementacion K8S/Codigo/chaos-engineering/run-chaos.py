#!/usr/bin/env python3
"""Ejecuta los seis escenarios de caos del obligatorio sobre PharmaGo."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any


GATEWAY = "pharmago-api-gateway"
PHARMACY = "pharmago-pharmacy-service"
SCENARIOS = ("requests", "cpu", "memory", "storage", "network", "component")
IMAGE = "busybox:1.36"


class ChaosError(RuntimeError):
    """Fallo de precondición, inyección o restauración."""


def note(scenario: str, message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{stamp} [{scenario}] {message}", flush=True)


class Kubectl:
    def __init__(self, namespace: str) -> None:
        self.namespace = namespace

    def run(self, *args: str, input_text: str | None = None) -> str:
        command = ["kubectl", *args]
        result = subprocess.run(command, input=input_text, text=True, capture_output=True)
        if result.returncode:
            lines = (result.stderr or result.stdout).strip().splitlines()
            detail = lines[-1] if lines else f"exit code {result.returncode}"
            raise ChaosError(f"{' '.join(command)}: {detail}")
        return result.stdout.strip()

    def get(self, kind: str, name: str | None = None, *, selector: str | None = None) -> dict[str, Any]:
        args = ["get", kind]
        if name:
            args.append(name)
        args += ["-n", self.namespace, "-o", "json"]
        if selector:
            args += ["-l", selector]
        return json.loads(self.run(*args))

    def patch(self, kind: str, name: str, operations: list[dict[str, Any]]) -> None:
        self.run("patch", kind, name, "-n", self.namespace, "--type=json", "-p", json.dumps(operations))

    def scale(self, deployment: str, replicas: int) -> None:
        self.run("scale", f"deployment/{deployment}", "-n", self.namespace, f"--replicas={replicas}")


def ready_gateway_node(client: Kubectl) -> str:
    pods = client.get("pods", selector=f"app={GATEWAY}")["items"]
    for pod in pods:
        ready = any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for condition in pod.get("status", {}).get("conditions", [])
        )
        if ready and pod.get("spec", {}).get("nodeName"):
            return pod["spec"]["nodeName"]
    raise ChaosError(f"No hay un pod Ready de {GATEWAY} con nodo asignado.")


def pod_manifest(
    name: str,
    scenario: str,
    command: str,
    node_selector: dict[str, str],
    duration: int,
    *,
    memory_mib: int = 0,
    storage_mib: int = 0,
) -> dict[str, Any]:
    memory_limit = max(64, memory_mib + 24)
    storage_limit = max(64, storage_mib + 32)
    volumes: list[dict[str, Any]] = []
    mounts: list[dict[str, str]] = []
    if memory_mib:
        volumes.append({"name": "load", "emptyDir": {"medium": "Memory", "sizeLimit": f"{memory_mib + 16}Mi"}})
        mounts.append({"name": "load", "mountPath": "/chaos"})
    if storage_mib:
        volumes.append({"name": "load", "emptyDir": {"sizeLimit": f"{storage_mib + 16}Mi"}})
        mounts.append({"name": "load", "mountPath": "/chaos"})
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "labels": {
                "app.kubernetes.io/name": "pharmago-chaos",
                "app.kubernetes.io/part-of": "pharmago",
                "chaos.pharmago/scenario": scenario,
            },
        },
        "spec": {
            "restartPolicy": "Never",
            "terminationGracePeriodSeconds": 1,
            "activeDeadlineSeconds": duration + 90,
            "nodeSelector": node_selector,
            "containers": [{
                "name": "load",
                "image": IMAGE,
                "imagePullPolicy": "IfNotPresent",
                "command": ["sh", "-ec", command],
                "resources": {
                    "requests": {"cpu": "25m", "memory": "16Mi", "ephemeral-storage": "16Mi"},
                    "limits": {
                        "cpu": "1",
                        "memory": f"{memory_limit}Mi",
                        "ephemeral-storage": f"{storage_limit}Mi",
                    },
                },
                "volumeMounts": mounts,
            }],
            "volumes": volumes,
        },
    }


def run_pod(client: Kubectl, manifest: dict[str, Any], scenario: str, duration: int) -> str:
    name = manifest["metadata"]["name"]
    created = False
    try:
        client.run("create", "-n", client.namespace, "-f", "-", input_text=json.dumps(manifest))
        created = True
        note(scenario, f"Pod {name} creado; esperando resultado.")
        deadline = time.monotonic() + duration + 90
        while time.monotonic() < deadline:
            pod = client.get("pod", name)
            phase = pod.get("status", {}).get("phase")
            if phase in ("Succeeded", "Failed"):
                logs = client.run("logs", name, "-n", client.namespace)
                if logs:
                    note(scenario, logs)
                if phase == "Failed":
                    raise ChaosError(f"El pod {name} terminó en Failed.")
                return logs
            time.sleep(2)
        raise ChaosError(f"El pod {name} excedió {duration + 90} s de espera.")
    finally:
        if created:
            client.run("delete", "pod", name, "-n", client.namespace,
                       "--ignore-not-found", "--wait=true", "--timeout=30s")
            note(scenario, f"Pod {name} eliminado.")


def request_command(duration: int, count: int, *, interval: int = 0) -> str:
    # wget de BusyBox devuelve código no cero para respuestas HTTP de error.
    return f"""
end=$(($(date +%s) + {duration}))
sent=0; success=0; other=0
while [ "$(date +%s)" -lt "$end" ] && [ "$sent" -lt {count} ]; do
  response=$(wget -S -q -T 2 -O /dev/null http://{GATEWAY}/api/drug 2>&1) || true
  code=$(printf '%s\\n' "$response" | sed -n 's/.*HTTP[^ ]* \\([0-9][0-9][0-9]\\).*/\\1/p' | tail -1)
  [ -n "$code" ] || code=ERR
  sent=$((sent + 1))
  if [ "$code" = 200 ]; then success=$((success + 1)); else other=$((other + 1)); fi
  echo "request=$sent status=$code"
  {f'sleep {interval}' if interval else ':'}
done
echo "SUMMARY sent=$sent success=$success other=$other"
""".strip()


def new_manifest(scenario: str, command: str, selector: dict[str, str], duration: int,
                 **kwargs: int) -> dict[str, Any]:
    name = f"pharmago-chaos-{scenario}-{uuid.uuid4().hex[:8]}"
    return pod_manifest(name, scenario, command, selector, duration, **kwargs)


def run_probe(client: Kubectl, scenario: str, duration: int, *, flood: bool = False,
              request_count: int = 200) -> tuple[int, int, int]:
    command = request_command(duration, request_count if flood else duration, interval=0 if flood else 1)
    manifest = new_manifest(scenario, command, {"node-type": "ops"}, duration)
    logs = run_pod(client, manifest, scenario, duration)
    summary = next((line for line in logs.splitlines() if line.startswith("SUMMARY ")), "")
    if not summary:
        raise ChaosError(f"El pod de {scenario} no produjo un resumen de requests.")
    values = dict(item.split("=", 1) for item in summary.split()[1:])
    sent, success, other = (int(values[key]) for key in ("sent", "success", "other"))
    if sent == 0:
        raise ChaosError(f"El pod de {scenario} no envió requests.")
    return sent, success, other


def resource_scenario(client: Kubectl, scenario: str, selector: dict[str, str],
                      duration: int, size_mib: int = 0) -> None:
    if scenario == "cpu":
        command = f"(while :; do :; done) & (while :; do :; done) & sleep {duration}; echo 'CPU load finished'"
        limits = {}
    elif scenario == "memory":
        command = f"dd if=/dev/zero of=/chaos/fill bs=1M count={size_mib}; sleep {duration}; echo 'Memory load finished'"
        limits = {"memory_mib": size_mib}
    else:
        command = f"dd if=/dev/zero of=/chaos/fill bs=1M count={size_mib}; sleep {duration}; echo 'Storage load finished'"
        limits = {"storage_mib": size_mib}
    note(scenario, f"Inyectando carga durante {duration} s en el nodo del gateway.")
    run_pod(client, new_manifest(scenario, command, selector, duration, **limits), scenario, duration)


def network_scenario(client: Kubectl, duration: int) -> None:
    _, baseline_success, _ = run_probe(client, "network-baseline", 5)
    if baseline_success == 0:
        raise ChaosError("La ruta del gateway a pharmacy ya falla antes de la inyección de red.")
    service = client.get("service", PHARMACY)
    ports = service.get("spec", {}).get("ports", [])
    if len(ports) != 1 or ports[0].get("protocol", "TCP") != "TCP":
        raise ChaosError("La prueba de red requiere un Service de pharmacy con un solo puerto TCP.")
    original = ports[0].get("targetPort", ports[0]["port"])
    if original == 1:
        raise ChaosError("El targetPort ya es 1; no se puede cortar esa ruta de forma controlada.")
    path = "/spec/ports/0/targetPort"
    note("network", f"Cortando temporalmente Service {PHARMACY}: targetPort {original} → 1.")
    client.patch("service", PHARMACY, [
        {"op": "test", "path": path, "value": original},
        {"op": "add", "path": path, "value": 1},
    ])
    try:
        _, _, other = run_probe(client, "network", duration)
        if other == 0:
            raise ChaosError("La ruta cortada no produjo respuestas fallidas; revisar la prueba.")
    finally:
        client.patch("service", PHARMACY, [
            {"op": "test", "path": path, "value": 1},
            {"op": "replace", "path": path, "value": original},
        ])
        note("network", f"Service {PHARMACY} restaurado a targetPort {original}.")
    _, success, _ = run_probe(client, "network-recovery", 12)
    if success == 0:
        raise ChaosError("No hubo respuesta 200 después de restaurar la red.")


def wait_for_zero_replicas(client: Kubectl, timeout: int = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        deployment = client.get("deployment", PHARMACY)
        pods = client.get("pods", selector=f"app={PHARMACY}")["items"]
        ready = any(
            condition.get("type") == "Ready" and condition.get("status") == "True"
            for pod in pods for condition in pod.get("status", {}).get("conditions", [])
        )
        if deployment.get("status", {}).get("replicas", 0) == 0 and not ready:
            return
        time.sleep(2)
    raise ChaosError(f"{PHARMACY} no llegó a 0 réplicas en {timeout} s.")


def component_scenario(client: Kubectl, duration: int) -> None:
    _, baseline_success, _ = run_probe(client, "component-baseline", 5)
    if baseline_success == 0:
        raise ChaosError("La ruta del gateway a pharmacy ya falla antes de desconectar el componente.")
    deployment = client.get("deployment", PHARMACY)
    original = deployment.get("spec", {}).get("replicas", 1)
    if original < 1:
        raise ChaosError(f"{PHARMACY} ya está escalado a cero.")
    note("component", f"Desconectando {PHARMACY}: {original} → 0 réplicas.")
    client.scale(PHARMACY, 0)
    try:
        wait_for_zero_replicas(client)
        _, _, other = run_probe(client, "component", duration)
        if other == 0:
            raise ChaosError("La desconexión no produjo respuestas fallidas; revisar la prueba.")
    finally:
        client.scale(PHARMACY, original)
        note("component", f"Restaurando {PHARMACY} a {original} réplicas.")
        client.run("rollout", "status", f"deployment/{PHARMACY}", "-n", client.namespace, "--timeout=180s")
    _, success, _ = run_probe(client, "component-recovery", 12)
    if success == 0:
        raise ChaosError("No hubo respuesta 200 después de restaurar el componente.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=(*SCENARIOS, "all"), required=True)
    parser.add_argument("--namespace", default="pharmago")
    parser.add_argument("--duration", type=int, default=20, help="Segundos por escenario (5 a 120)")
    parser.add_argument("--requests", type=int, default=200, help="Máximo de requests en la inyección")
    parser.add_argument("--memory-mib", type=int, default=96)
    parser.add_argument("--storage-mib", type=int, default=128)
    args = parser.parse_args(argv)
    if not 5 <= args.duration <= 120:
        parser.error("--duration debe estar entre 5 y 120 segundos")
    if not 1 <= args.requests <= 1000:
        parser.error("--requests debe estar entre 1 y 1000")
    if not 16 <= args.memory_mib <= 256:
        parser.error("--memory-mib debe estar entre 16 y 256")
    if not 16 <= args.storage_mib <= 512:
        parser.error("--storage-mib debe estar entre 16 y 512")
    return args


def run(args: argparse.Namespace) -> None:
    client = Kubectl(args.namespace)
    context = client.run("config", "current-context")
    note("preflight", f"Contexto={context}, namespace={args.namespace}.")
    client.run("get", "namespace", args.namespace, "-o", "json")
    for name in (GATEWAY, PHARMACY):
        deployment = client.get("deployment", name)
        desired = deployment.get("spec", {}).get("replicas", 1)
        available = deployment.get("status", {}).get("availableReplicas", 0)
        if desired < 1 or available < desired:
            raise ChaosError(f"{name} no está completamente disponible ({available}/{desired}).")
    client.get("service", GATEWAY)
    client.get("service", PHARMACY)
    ops = json.loads(client.run("get", "nodes", "-l", "node-type=ops", "-o", "json"))["items"]
    if len(ops) != 1:
        raise ChaosError("Se requiere exactamente un nodo node-type=ops para las sondas.")
    ops_ready = any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in ops[0].get("status", {}).get("conditions", [])
    )
    if not ops_ready or ops[0].get("spec", {}).get("unschedulable", False):
        raise ChaosError("El nodo ops debe estar Ready y disponible para programar las sondas.")
    node = ready_gateway_node(client)
    node_info = json.loads(client.run("get", "node", node, "-o", "json"))
    hostname = node_info.get("metadata", {}).get("labels", {}).get("kubernetes.io/hostname")
    if not hostname:
        raise ChaosError(f"El nodo {node} no tiene etiqueta kubernetes.io/hostname.")
    node_ready = any(
        condition.get("type") == "Ready" and condition.get("status") == "True"
        for condition in node_info.get("status", {}).get("conditions", [])
    )
    if not node_ready or node_info.get("spec", {}).get("unschedulable", False):
        raise ChaosError(f"El nodo {node} debe estar Ready y disponible para los pods de carga.")
    selector = {"kubernetes.io/hostname": hostname}
    note("preflight", f"Nodo del gateway={node}. Los pods de carga usarán ese nodo; las sondas, ops.")

    scenarios = SCENARIOS if args.scenario == "all" else (args.scenario,)
    for scenario in scenarios:
        if scenario == "requests":
            note(scenario, f"Inyectando hasta {args.requests} requests durante {args.duration} s.")
            run_probe(client, scenario, args.duration, flood=True, request_count=args.requests)
        elif scenario == "cpu":
            resource_scenario(client, scenario, selector, args.duration)
        elif scenario == "memory":
            resource_scenario(client, scenario, selector, args.duration, args.memory_mib)
        elif scenario == "storage":
            resource_scenario(client, scenario, selector, args.duration, args.storage_mib)
        elif scenario == "network":
            network_scenario(client, args.duration)
        else:
            component_scenario(client, args.duration)
        note(scenario, "Escenario completado.")
    note("done", f"Completados {len(scenarios)} escenarios: {', '.join(scenarios)}.")


def _on_term(_signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, _on_term)
    args = parse_args(argv)
    try:
        run(args)
    except (ChaosError, KeyboardInterrupt) as error:
        print(f"Error: {error or 'interrumpido'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
