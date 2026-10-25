import json
import tempfile
import unittest
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import numpy as np
import pandas as pd

from pipeline import AnalysisConfig, analyze_dataset, load_csv, save_artifacts


class AnalysisTests(unittest.TestCase):
    @staticmethod
    def sample() -> pd.DataFrame:
        rng = np.random.default_rng(7)
        normal = rng.normal(0, 0.3, size=(90, 2))
        anomalies = rng.normal(7, 0.2, size=(10, 2))
        values = np.vstack([normal, anomalies])
        return pd.DataFrame({
            "timestamp": pd.date_range("2026-01-01", periods=100, freq="min", tz="UTC"),
            "cpu": values[:, 0],
            "memory": values[:, 1],
            "constant": 1,
            "label": np.r_[np.zeros(90, dtype=int), np.ones(10, dtype=int)],
        })

    def test_both_models_detect_injected_anomalies_without_label_leakage(self) -> None:
        frame = self.sample()
        config = AnalysisConfig(contamination=0.1, nu=0.1, gamma="scale", random_state=7)
        result = analyze_dataset(
            frame, config, timestamp_column="timestamp", label_column="label", anomaly_label="1"
        )

        self.assertEqual(result.feature_columns, ["cpu", "memory"])
        self.assertEqual(result.summary["dropped_empty_or_constant_features"], ["constant"])
        self.assertEqual(len(result.scored), 100)
        self.assertGreaterEqual(result.scored.loc[90:, "isolation_forest_anomaly"].sum(), 8)
        self.assertGreaterEqual(result.scored.loc[90:, "one_class_svm_rbf_anomaly"].sum(), 5)
        self.assertIn("f1", result.summary["models"]["one_class_svm_rbf"]["labeled_evaluation_in_sample"])
        self.assertEqual(
            result.summary["models"]["isolation_forest"]["labeled_evaluation_in_sample"]["labeled_anomalies"],
            10,
        )
        self.assertEqual(result.summary["comparison"]["both_anomalous"], int(
            ((result.scored["isolation_forest_anomaly"] == 1)
             & (result.scored["one_class_svm_rbf_anomaly"] == 1)).sum()
        ))

        repeated = analyze_dataset(frame, config, timestamp_column="timestamp", label_column="label")
        pd.testing.assert_series_equal(
            result.scored["isolation_forest_anomaly"], repeated.scored["isolation_forest_anomaly"]
        )

    def test_missing_values_training_cap_and_artifacts(self) -> None:
        frame = self.sample().drop(columns="label")
        frame.loc[4, "cpu"] = np.nan
        config = AnalysisConfig(max_train_rows=25)
        result = analyze_dataset(frame, config, timestamp_column="timestamp")
        self.assertEqual(result.summary["training_rows"], 25)
        self.assertEqual(result.summary["missing_values_by_feature"]["cpu"], 1)

        with tempfile.TemporaryDirectory() as directory:
            paths = save_artifacts(result, directory, plot_feature="cpu", timestamp_column="timestamp")
            self.assertTrue(all(path.exists() and path.stat().st_size > 0 for path in paths.values()))
            summary = json.loads(Path(paths["summary"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["rows"], 100)
            self.assertEqual(len(pd.read_csv(paths["results"])), 100)

    def test_invalid_or_unusable_datasets_fail_clearly(self) -> None:
        with self.assertRaisesRegex(ValueError, "al menos 10"):
            analyze_dataset(self.sample().head(5))
        with self.assertRaisesRegex(ValueError, "vacías o son constantes"):
            analyze_dataset(pd.DataFrame({"constant": [1] * 20}))
        with self.assertRaisesRegex(ValueError, "inexistentes"):
            analyze_dataset(self.sample(), feature_columns=["unknown"])
        with self.assertRaisesRegex(ValueError, "requiere label_column"):
            analyze_dataset(self.sample(), anomaly_label="1")

    def test_numeric_labels_and_unlabeled_rows(self) -> None:
        frame = self.sample()
        frame["label"] = frame["label"].astype(float)
        frame.loc[0, "label"] = np.nan
        result = analyze_dataset(frame, label_column="label", anomaly_label="1")
        evaluation = result.summary["models"]["isolation_forest"]["labeled_evaluation_in_sample"]
        self.assertEqual(evaluation["labeled_rows"], 99)
        self.assertEqual(evaluation["labeled_anomalies"], 10)

    def test_teacher_style_csv_with_semicolon_and_decimal_comma(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "docente.csv"
            path.write_text("cpu;memoria\n1,5;2,5\n3,5;4,5\n", encoding="utf-8")
            frame = load_csv(path, delimiter=";", decimal=",")
            self.assertEqual(frame.loc[0, "cpu"], 1.5)
            self.assertEqual(frame.loc[1, "memoria"], 4.5)


if __name__ == "__main__":
    unittest.main()
