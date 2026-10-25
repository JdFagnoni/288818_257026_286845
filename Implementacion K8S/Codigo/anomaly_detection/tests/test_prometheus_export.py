import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from prometheus_export import export_node_metrics, node_queries, query_range


class PrometheusExportTests(unittest.TestCase):
    def test_queries_filter_one_node_and_preserve_node_label(self) -> None:
        queries = node_queries('pharmago-m02"evil')
        self.assertEqual(len(queries), 6)
        for expression in queries.values():
            self.assertIn('node="pharmago-m02\\"evil"', expression)
            self.assertIn('job="node-exporter"', expression)

    @patch("prometheus_export.query_range")
    def test_export_aligns_series_and_omits_unavailable_metrics(self, mocked_query) -> None:
        mocked_query.side_effect = [
            [{"values": [[1000, "20"], [1060, "30"]]}],
            [{"values": [[1060, "55"], [1120, "56"]]}],
            [], [], [], [],
        ]
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 1, tzinfo=timezone.utc)
        frame = export_node_metrics("http://prometheus:9090", "pharmago-m02", start, end)
        self.assertEqual(len(frame), 3)
        self.assertEqual(frame.columns.tolist(), ["timestamp", "node", "cpu_percent", "memory_percent"])
        self.assertEqual(frame["node"].unique().tolist(), ["pharmago-m02"])
        self.assertEqual(frame.loc[1, "cpu_percent"], 30)
        self.assertEqual(frame.loc[1, "memory_percent"], 55)

    def test_invalid_query_window(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "start < end"):
            query_range("http://localhost:9090", "up", start, start, 60)


if __name__ == "__main__":
    unittest.main()
