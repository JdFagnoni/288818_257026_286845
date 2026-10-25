"""CLI para exportar métricas de Prometheus y comparar detectores de anomalías."""

from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from pipeline import AnalysisConfig, analyze_dataset, load_csv, save_artifacts  # noqa: E402
from prometheus_export import export_node_metrics, utc_now  # noqa: E402


HERE = Path(__file__).resolve().parent


def parse_gamma(value: str) -> str | float:
    if value in ("scale", "auto"):
        return value
    try:
        return float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("gamma debe ser 'scale', 'auto' o un número positivo.") from error


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export-prometheus", help="Guardar métricas de un nodo como CSV")
    export.add_argument("--url", default="http://127.0.0.1:9090", help="URL de Prometheus")
    export.add_argument("--node", required=True, help="Nombre del nodo Kubernetes")
    export.add_argument("--hours", type=float, default=6, help="Horas de historial (defecto: 6)")
    export.add_argument("--step", type=int, default=60, help="Paso en segundos (defecto: 60)")
    export.add_argument("--output", type=Path, required=True, help="Ruta del CSV de salida")

    analyze = commands.add_parser("analyze", help="Ajustar ambos modelos y comparar anomalías")
    analyze.add_argument("--input", type=Path, default=HERE / "examples/prometheus_sample.csv")
    analyze.add_argument("--output-dir", type=Path, default=Path("output/sample"))
    analyze.add_argument("--delimiter", default=",")
    analyze.add_argument("--decimal", default=".")
    analyze.add_argument("--features", nargs="+", metavar="COLUMNA", help="Métricas numéricas; por defecto se detectan")
    analyze.add_argument("--timestamp-column", help="Columna de fecha/hora ISO 8601")
    analyze.add_argument("--label-column", help="Columna de etiquetas; se excluye del entrenamiento")
    analyze.add_argument("--anomaly-label", help="Valor que indica anomalía en la columna de etiquetas")
    analyze.add_argument("--plot-feature", help="Métrica a mostrar en la gráfica")
    analyze.add_argument("--contamination", type=float, default=0.05)
    analyze.add_argument("--nu", type=float, default=0.05)
    analyze.add_argument("--gamma", type=parse_gamma, default="scale")
    analyze.add_argument("--n-estimators", type=int, default=200)
    analyze.add_argument("--random-state", type=int, default=42)
    analyze.add_argument("--max-train-rows", type=int, default=5_000)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    if args.command == "export-prometheus":
        if args.hours <= 0:
            raise SystemExit("--hours debe ser positivo.")
        end = utc_now().replace(microsecond=0)
        start = end - timedelta(hours=args.hours)
        frame = export_node_metrics(args.url, args.node, start, end, args.step)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(args.output, index=False)
        print(f"Exportadas {len(frame)} filas y {len(frame.columns) - 2} métricas a {args.output}")
        return 0

    config = AnalysisConfig(
        contamination=args.contamination,
        nu=args.nu,
        gamma=args.gamma,
        n_estimators=args.n_estimators,
        random_state=args.random_state,
        max_train_rows=args.max_train_rows,
    )
    frame = load_csv(args.input, delimiter=args.delimiter, decimal=args.decimal)
    timestamp = args.timestamp_column or ("timestamp" if "timestamp" in frame else None)
    result = analyze_dataset(
        frame,
        config,
        feature_columns=args.features,
        timestamp_column=timestamp,
        label_column=args.label_column,
        anomaly_label=args.anomaly_label,
    )
    paths = save_artifacts(
        result, args.output_dir, plot_feature=args.plot_feature, timestamp_column=timestamp
    )
    print(f"Filas: {result.summary['rows']}; entrenamiento: {result.summary['training_rows']}")
    print(f"Métricas: {', '.join(result.feature_columns)}")
    for name, details in result.summary["models"].items():
        print(f"{name}: {details['anomalies']} anomalías ({details['anomaly_fraction']:.1%})")
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
