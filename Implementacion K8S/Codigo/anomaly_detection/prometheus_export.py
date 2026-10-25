"""Exporta un CSV de métricas de un nodo sin alterar el clúster Kubernetes."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd


def node_queries(node: str) -> dict[str, str]:
    # json.dumps produce una cadena PromQL entre comillas y escapa el nombre.
    selector = f'node={json.dumps(node)}'
    return {
        "cpu_percent": (
            f'100 * (1 - avg by (node) (rate(node_cpu_seconds_total{{{selector},job="node-exporter",mode="idle"}}[5m])))'
        ),
        "memory_percent": (
            f'100 * (1 - node_memory_MemAvailable_bytes{{{selector},job="node-exporter"}} '
            f'/ node_memory_MemTotal_bytes{{{selector},job="node-exporter"}})'
        ),
        "disk_available_percent": (
            f'min by (node) (100 * node_filesystem_avail_bytes{{{selector},job="node-exporter",mountpoint=~"/|/data",fstype!~"tmpfs|overlay|squashfs"}} '
            f'/ node_filesystem_size_bytes{{{selector},job="node-exporter",mountpoint=~"/|/data",fstype!~"tmpfs|overlay|squashfs"}})'
        ),
        "network_receive_bytes_per_second": (
            f'sum by (node) (rate(node_network_receive_bytes_total{{{selector},job="node-exporter",device!="lo"}}[5m]))'
        ),
        "network_transmit_bytes_per_second": (
            f'sum by (node) (rate(node_network_transmit_bytes_total{{{selector},job="node-exporter",device!="lo"}}[5m]))'
        ),
        "network_errors_per_second": (
            f'sum by (node) (rate(node_network_receive_errs_total{{{selector},job="node-exporter",device!="lo"}}[5m]) '
            f'+ rate(node_network_transmit_errs_total{{{selector},job="node-exporter",device!="lo"}}[5m]))'
        ),
    }


def query_range(
    base_url: str,
    expression: str,
    start: datetime,
    end: datetime,
    step_seconds: int,
) -> list[dict]:
    if not 15 <= step_seconds <= 3600:
        raise ValueError("step_seconds debe estar entre 15 y 3600.")
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("start y end deben tener zona horaria y start < end.")

    params = urlencode({
        "query": expression,
        "start": start.timestamp(),
        "end": end.timestamp(),
        "step": step_seconds,
    })
    url = f"{base_url.rstrip('/')}/api/v1/query_range?{params}"
    with urlopen(url, timeout=30) as response:
        payload = json.load(response)
    if payload.get("status") != "success" or payload.get("data", {}).get("resultType") != "matrix":
        raise RuntimeError(f"Prometheus rechazó query_range: {payload.get('error', payload)}")
    return payload["data"]["result"]


def export_node_metrics(
    base_url: str,
    node: str,
    start: datetime,
    end: datetime,
    step_seconds: int = 60,
) -> pd.DataFrame:
    """Devuelve muestras alineadas por instante y listas para usar como CSV."""
    if not node.strip():
        raise ValueError("El nombre de nodo no puede estar vacío.")
    columns: dict[str, pd.Series] = {}
    for name, expression in node_queries(node).items():
        result = query_range(base_url, expression, start, end, step_seconds)
        if len(result) > 1:
            raise RuntimeError(f"La consulta {name} devolvió más de una serie para {node}.")
        if result:
            values = {float(timestamp): float(value) for timestamp, value in result[0]["values"]}
            columns[name] = pd.Series(values, dtype="float64")

    if not columns:
        raise ValueError(f"Prometheus no devolvió métricas para {node}; verificá nodo, puerto y rango.")

    frame = pd.DataFrame(columns).sort_index()
    frame.index = pd.to_datetime(frame.index, unit="s", utc=True)
    frame.index.name = "timestamp"
    frame = frame.reset_index()
    frame.insert(1, "node", node)
    frame["timestamp"] = frame["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return frame


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
