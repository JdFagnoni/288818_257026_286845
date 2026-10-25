"""Análisis exploratorio de anomalías sobre métricas tabulares numéricas."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.preprocessing import StandardScaler
from sklearn.svm import OneClassSVM


ISOLATION_FOREST = "isolation_forest"
ONE_CLASS_SVM = "one_class_svm_rbf"
MODEL_NAMES = (ISOLATION_FOREST, ONE_CLASS_SVM)
AUTO_EXCLUDED = {"label", "target", "class", "anomaly", "is_anomaly", "id", "index"}


@dataclass(frozen=True)
class AnalysisConfig:
    contamination: float = 0.05
    nu: float = 0.05
    gamma: str | float = "scale"
    n_estimators: int = 200
    random_state: int = 42
    max_train_rows: int = 5_000

    def __post_init__(self) -> None:
        if not 0 < self.contamination <= 0.5:
            raise ValueError("contamination debe estar en (0, 0.5].")
        if not 0 < self.nu <= 1:
            raise ValueError("nu debe estar en (0, 1].")
        if self.gamma not in ("scale", "auto") and not (
            isinstance(self.gamma, (int, float)) and self.gamma > 0
        ):
            raise ValueError("gamma debe ser 'scale', 'auto' o un número positivo.")
        if self.n_estimators < 1:
            raise ValueError("n_estimators debe ser positivo.")
        if self.max_train_rows < 10:
            raise ValueError("max_train_rows debe ser al menos 10.")


@dataclass
class AnalysisResult:
    scored: pd.DataFrame
    summary: dict[str, Any]
    feature_columns: list[str]


def load_csv(path: str | Path, *, delimiter: str = ",", decimal: str = ".") -> pd.DataFrame:
    """Carga un CSV sin asumir la estructura de los datasets docentes."""
    frame = pd.read_csv(path, sep=delimiter, decimal=decimal)
    if frame.empty:
        raise ValueError("El dataset no tiene filas.")
    return frame


def build_models(config: AnalysisConfig) -> dict[str, object]:
    return {
        ISOLATION_FOREST: IsolationForest(
            n_estimators=config.n_estimators,
            contamination=config.contamination,
            random_state=config.random_state,
            n_jobs=-1,
        ),
        ONE_CLASS_SVM: OneClassSVM(kernel="rbf", nu=config.nu, gamma=config.gamma),
    }


def _select_features(
    frame: pd.DataFrame,
    feature_columns: list[str] | None,
    excluded: set[str],
) -> tuple[pd.DataFrame, list[str], list[str]]:
    if feature_columns is None:
        selected = [
            column
            for column in frame.select_dtypes(include="number").columns
            if column not in excluded and column.casefold() not in AUTO_EXCLUDED
        ]
    else:
        selected = list(dict.fromkeys(feature_columns))
        missing = set(selected) - set(frame.columns)
        if missing:
            raise ValueError(f"Columnas de métricas inexistentes: {sorted(missing)}")
        overlapping = set(selected) & excluded
        if overlapping:
            raise ValueError(f"Una columna de etiqueta/tiempo no puede ser métrica: {sorted(overlapping)}")

    if not selected:
        raise ValueError("No hay métricas numéricas; indicá --features con columnas numéricas.")

    numeric = frame[selected].apply(pd.to_numeric, errors="coerce")
    numeric = numeric.replace([np.inf, -np.inf], np.nan)
    dropped = [
        column for column in selected
        if numeric[column].notna().sum() == 0 or numeric[column].dropna().nunique() < 2
    ]
    kept = [column for column in selected if column not in dropped]
    if not kept:
        raise ValueError("Todas las métricas están vacías o son constantes.")
    return numeric[kept], kept, dropped


def _labeled_anomalies(values: pd.Series, anomaly_label: str) -> pd.Series:
    if pd.api.types.is_numeric_dtype(values):
        try:
            target = float(anomaly_label)
        except ValueError as error:
            raise ValueError("anomaly_label debe ser numérico para una columna numérica.") from error
        return pd.to_numeric(values, errors="coerce").eq(target)
    return values.astype(str).str.strip().str.casefold().eq(
        str(anomaly_label).strip().casefold()
    )


def analyze_dataset(
    frame: pd.DataFrame,
    config: AnalysisConfig = AnalysisConfig(),
    *,
    feature_columns: list[str] | None = None,
    timestamp_column: str | None = None,
    label_column: str | None = None,
    anomaly_label: str | None = None,
    models: Mapping[str, object] | None = None,
) -> AnalysisResult:
    """Ajusta ambos modelos y marca anomalías en todas las filas del dataset.

    Es un análisis no supervisado y exploratorio: se ajusta con el mismo
    dataset que se puntúa. Si hay etiquetas, las métricas son *in-sample*.
    """
    if len(frame) < 10:
        raise ValueError("Se necesitan al menos 10 filas para comparar los modelos.")
    for column in (timestamp_column, label_column):
        if column is not None and column not in frame.columns:
            raise ValueError(f"No existe la columna {column!r}.")
    if anomaly_label is not None and label_column is None:
        raise ValueError("anomaly_label requiere label_column.")
    if timestamp_column is not None:
        pd.to_datetime(frame[timestamp_column], errors="raise", utc=True)

    excluded = {column for column in (timestamp_column, label_column) if column}
    numeric, features, dropped = _select_features(frame, feature_columns, excluded)
    transformed = StandardScaler().fit_transform(SimpleImputer(strategy="median").fit_transform(numeric))

    if len(frame) > config.max_train_rows:
        rng = np.random.default_rng(config.random_state)
        train_positions = np.sort(rng.choice(len(frame), config.max_train_rows, replace=False))
    else:
        train_positions = np.arange(len(frame))
    training = transformed[train_positions]

    estimators = dict(models) if models is not None else build_models(config)
    if set(estimators) != set(MODEL_NAMES):
        raise ValueError(f"Se requieren estos modelos: {MODEL_NAMES}.")

    scored = frame.copy().reset_index(drop=True)
    model_summary: dict[str, Any] = {}
    for name in MODEL_NAMES:
        estimator = estimators[name]
        estimator.fit(training)
        predictions = estimator.predict(transformed)
        flags = predictions == -1
        # decision_function: menor valor = más anómalo. Invertimos el signo.
        scored[f"{name}_anomaly"] = flags.astype(int)
        scored[f"{name}_score"] = -estimator.decision_function(transformed)
        model_summary[name] = {
            "anomalies": int(flags.sum()),
            "anomaly_fraction": float(flags.mean()),
        }

        if label_column is not None and anomaly_label is not None:
            labeled = frame[label_column].notna()
            if not labeled.any():
                raise ValueError("La columna de etiquetas no contiene valores.")
            actual = _labeled_anomalies(frame.loc[labeled, label_column], anomaly_label)
            predicted = flags[labeled.to_numpy()]
            model_summary[name]["labeled_evaluation_in_sample"] = {
                "precision": float(precision_score(actual, predicted, zero_division=0)),
                "recall": float(recall_score(actual, predicted, zero_division=0)),
                "f1": float(f1_score(actual, predicted, zero_division=0)),
                "labeled_rows": int(labeled.sum()),
                "labeled_anomalies": int(actual.sum()),
            }

    forest_flags = scored[f"{ISOLATION_FOREST}_anomaly"].astype(bool)
    svm_flags = scored[f"{ONE_CLASS_SVM}_anomaly"].astype(bool)
    both = int((forest_flags & svm_flags).sum())
    either = int((forest_flags | svm_flags).sum())
    summary = {
        "rows": len(frame),
        "training_rows": len(training),
        "features": features,
        "dropped_empty_or_constant_features": dropped,
        "missing_values_by_feature": {column: int(numeric[column].isna().sum()) for column in features},
        "parameters": {
            "contamination": config.contamination,
            "nu": config.nu,
            "gamma": config.gamma,
            "n_estimators": config.n_estimators,
            "random_state": config.random_state,
            "max_train_rows": config.max_train_rows,
        },
        "models": model_summary,
        "comparison": {
            "both_anomalous": both,
            "either_anomalous": either,
            "agreement_fraction": float((forest_flags == svm_flags).mean()),
            "anomaly_jaccard": float(both / either) if either else 1.0,
        },
    }
    return AnalysisResult(scored=scored, summary=summary, feature_columns=features)


def plot_comparison(
    result: AnalysisResult,
    *,
    feature: str | None = None,
    timestamp_column: str | None = None,
) -> plt.Figure:
    """Dibuja la misma métrica con las anomalías de cada modelo marcadas."""
    feature = feature or result.feature_columns[0]
    if feature not in result.feature_columns:
        raise ValueError(f"{feature!r} no es una métrica utilizada por los modelos.")
    if timestamp_column is not None and timestamp_column not in result.scored:
        raise ValueError(f"No existe la columna {timestamp_column!r}.")

    x = (
        pd.to_datetime(result.scored[timestamp_column], utc=True)
        if timestamp_column else np.arange(len(result.scored))
    )
    y = pd.to_numeric(result.scored[feature], errors="coerce")
    fig, axes = plt.subplots(2, 1, figsize=(13, 7), sharex=True, layout="constrained")
    for axis, name, title in zip(
        axes,
        MODEL_NAMES,
        ("Isolation Forest", "One-Class SVM (RBF)"),
    ):
        anomalous = result.scored[f"{name}_anomaly"].astype(bool)
        axis.plot(x, y, linewidth=1, color="#3375a6", label="Métrica")
        axis.scatter(x[anomalous], y[anomalous], color="#d62728", s=28, label="Anomalía", zorder=3)
        axis.set(title=f"{title}: {int(anomalous.sum())} anomalías", ylabel=feature)
        axis.grid(alpha=0.2)
        axis.legend(loc="upper right")
    axes[-1].set_xlabel(timestamp_column or "Fila")
    return fig


def save_artifacts(
    result: AnalysisResult,
    output_dir: str | Path,
    *,
    plot_feature: str | None = None,
    timestamp_column: str | None = None,
) -> dict[str, Path]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    paths = {
        "results": output / "results.csv",
        "summary": output / "summary.json",
        "plot": output / "comparison.png",
    }
    result.scored.to_csv(paths["results"], index=False)
    paths["summary"].write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    figure = plot_comparison(result, feature=plot_feature, timestamp_column=timestamp_column)
    figure.savefig(paths["plot"], dpi=150)
    plt.close(figure)
    return paths
