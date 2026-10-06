"""Actual analysis stages and isolated numerical-failure regressions."""

import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from raymol_energy.analysis import Analysis
from raymol_energy.artifacts import Artifacts
from raymol_energy.solvation import Solvation
import test_preparation as fixtures


class WorkerTests(unittest.TestCase):
    def run_analysis(self, fail=None):
        snapshot = fixtures.PreparationTests.fixture()
        root = fixtures.PreparationTests.root / "run_261003_energy_feasibility/feature-261004/worker-tests" / snapshot.analysis_id
        store = Artifacts(root=root)
        payload = {"directory": str(root), "snapshot": store.write_json("snapshot.json", snapshot.model_dump(mode="json"))}
        events = []
        operation = Analysis()
        if fail:
            with patch.object(Solvation, fail, side_effect=ValueError("Injected numerical mismatch")):
                result = operation.run(payload, threading.Event(), events.append)
        else:
            result = operation.run(payload, threading.Event(), events.append)
        return events, result, store

    def test_real_four_component_artifacts_and_order(self):
        events, result, store = self.run_analysis()
        self.assertTrue(result["complete"])
        self.assertEqual([e["stage"] for e in events if e["status"] == "ready"], ["prepared", "direct", "global_solvation", "local_solvation", "strain"])
        self.assertEqual([e["sequence"] for e in events], list(range(1, 10)))
        for event in events:
            for descriptor in event["artifacts"].values(): store.read(descriptor)
            self.assertEqual(event["endpoint_hash"], result["endpoint_hash"])
        self.assertEqual(json.loads(store.read(result["result"]))["statuses"], result["statuses"])

    def test_failed_local_allocation_retains_global_and_finishes_strain(self):
        events, result, _ = self.run_analysis("local")
        self.assertFalse(result["complete"])
        self.assertEqual(result["statuses"]["global_solvation"], "ready")
        self.assertEqual(result["statuses"]["local_solvation"], "failed")
        self.assertEqual(result["statuses"]["strain"], "ready")
        self.assertTrue(any(e["stage"] == "local_solvation" and "Injected numerical mismatch" in e.get("message", "") for e in events))

    def test_failed_global_blocks_only_allocation(self):
        _, result, _ = self.run_analysis("global_totals")
        self.assertEqual(result["statuses"]["global_solvation"], "failed")
        self.assertEqual(result["statuses"]["local_solvation"], "unavailable")
        self.assertEqual(result["statuses"]["direct"], "ready")
        self.assertEqual(result["statuses"]["strain"], "ready")

    def test_cancelled_preparation_never_publishes(self):
        snapshot = fixtures.PreparationTests.fixture()
        root = fixtures.PreparationTests.root / "run_261003_energy_feasibility/feature-261004/worker-tests" / snapshot.analysis_id
        store = Artifacts(root=root)
        descriptor = store.write_json("snapshot.json", snapshot.model_dump(mode="json"))
        cancelled = threading.Event()
        cancelled.set()
        events = []
        with self.assertRaises(InterruptedError):
            Analysis().run({"directory": str(root), "snapshot": descriptor}, cancelled, events.append)
        self.assertEqual(events, [])
