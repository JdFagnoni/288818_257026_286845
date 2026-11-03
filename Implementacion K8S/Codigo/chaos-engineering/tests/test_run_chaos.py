"""Comprobaciones locales de aislamiento y restauración de los experimentos."""

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "run-chaos.py"
spec = importlib.util.spec_from_file_location("run_chaos", SCRIPT)
chaos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(chaos)


class FakeClient:
    namespace = "pharmago"

    def __init__(self):
        self.calls = []

    def run(self, *args, **kwargs):
        self.calls.append(("run", args))
        if args[0] == "logs":
            return "failure in load pod"
        return ""

    def get(self, kind, name=None, **_kwargs):
        if kind == "service":
            return {"spec": {"ports": [{"port": 80, "targetPort": 80, "protocol": "TCP"}]}}
        if kind == "deployment":
            return {"spec": {"replicas": 2}}
        if kind == "pod":
            return {"status": {"phase": "Failed"}}
        raise AssertionError(f"get inesperado: {kind} {name}")

    def patch(self, kind, name, operations):
        self.calls.append(("patch", kind, name, operations))

    def scale(self, deployment, replicas):
        self.calls.append(("scale", deployment, replicas))


class ChaosTests(unittest.TestCase):
    def test_load_pods_have_limits_and_do_not_take_microservice_labels(self):
        selector = {"kubernetes.io/hostname": "pharmago-m03"}
        memory = chaos.pod_manifest("chaos-memory", "memory", "sleep 5", selector, 5,
                                    memory_mib=96)
        storage = chaos.pod_manifest("chaos-storage", "storage", "sleep 5", selector, 5,
                                     storage_mib=128)
        self.assertEqual(memory["spec"]["nodeSelector"], selector)
        self.assertEqual(memory["spec"]["volumes"][0]["emptyDir"]["medium"], "Memory")
        self.assertEqual(storage["spec"]["volumes"][0]["emptyDir"]["sizeLimit"], "144Mi")
        self.assertNotIn("workload-class", memory["metadata"]["labels"])
        self.assertEqual(memory["spec"]["containers"][0]["resources"]["limits"]["memory"], "120Mi")

    def test_temporary_pod_is_deleted_after_failure(self):
        client = FakeClient()
        manifest = chaos.pod_manifest("chaos-fail", "cpu", "false", {"node-type": "ops"}, 5)
        with self.assertRaisesRegex(chaos.ChaosError, "Failed"):
            chaos.run_pod(client, manifest, "cpu", 5)
        self.assertEqual(client.calls[0][1][0], "create")
        self.assertEqual(client.calls[-1][1][:3], ("delete", "pod", "chaos-fail"))

    def test_network_port_is_restored_when_probe_fails(self):
        client = FakeClient()
        with patch.object(chaos, "run_probe", side_effect=[(1, 1, 0), chaos.ChaosError("sonda falló")]) as probe:
            with self.assertRaisesRegex(chaos.ChaosError, "sonda falló"):
                chaos.network_scenario(client, 5)
        self.assertEqual(
            [call.kwargs["url"] for call in probe.call_args_list],
            [chaos.PHARMACY_HEALTH_URL, chaos.PHARMACY_HEALTH_URL],
        )
        patches = [call for call in client.calls if call[0] == "patch"]
        self.assertEqual(len(patches), 2)
        self.assertEqual(patches[0][3][-1]["value"], 1)
        self.assertEqual(patches[1][3][-1]["value"], 80)

    def test_component_replicas_are_restored_when_probe_fails(self):
        client = FakeClient()
        with patch.object(chaos, "run_probe", side_effect=[(1, 1, 0), chaos.ChaosError("sonda falló")]):
            with patch.object(chaos, "wait_for_zero_replicas"):
                with self.assertRaisesRegex(chaos.ChaosError, "sonda falló"):
                    chaos.component_scenario(client, 5)
        scales = [call for call in client.calls if call[0] == "scale"]
        self.assertEqual(scales, [("scale", chaos.PHARMACY, 0), ("scale", chaos.PHARMACY, 2)])
        self.assertTrue(any(call[0] == "run" and call[1][0] == "rollout" for call in client.calls))


if __name__ == "__main__":
    unittest.main()
