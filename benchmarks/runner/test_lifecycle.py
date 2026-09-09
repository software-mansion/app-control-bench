#!/usr/bin/env python3
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench
import sim_device


class ToolSymmetryTests(unittest.TestCase):
    def test_every_tool_has_an_explicit_runtime_policy(self):
        self.assertEqual(set(bench.TOOLS), set(bench.TOOL_RUNTIME))
        for policy in bench.TOOL_RUNTIME.values():
            self.assertEqual(
                set(policy),
                {"state_env", "detached_cleanup", "operator_health"},
            )

    def test_expected_provenance_has_the_same_shape_for_every_tool(self):
        with mock.patch.object(bench, "run_versions", return_value={}):
            shapes = [set(bench.expected_versions(tool, "element")) for tool in bench.TOOLS]
        self.assertTrue(all(shape == shapes[0] for shape in shapes))


class SimulatorOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.ownership = os.path.join(self.temporary.name, "owned.json")
        self.patch = mock.patch.object(sim_device, "OWNERSHIP_FILE", self.ownership)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temporary.cleanup()

    def test_foreign_device_is_never_a_cleanup_target(self):
        with mock.patch.object(sim_device, "_simctl") as simctl:
            self.assertFalse(sim_device.destroy("FOREIGN"))
        simctl.assert_not_called()

    def test_exact_journaled_identity_is_required(self):
        sim_device._record_clone("owned-name", "OWNED")
        devices = [
            {"name": "different-name", "udid": "OWNED", "state": "Shutdown", "runtime": "ios"},
            {"name": "golden", "udid": "GOLDEN", "state": "Shutdown", "runtime": "ios"},
        ]
        with mock.patch.object(sim_device, "_devices", return_value=devices), \
             mock.patch.object(sim_device, "golden_info", return_value=devices[1]), \
             mock.patch.object(sim_device, "_simctl") as simctl:
            self.assertFalse(sim_device.destroy("OWNED"))
        simctl.assert_not_called()

    def test_foreign_booted_devices_are_observed_without_mutation(self):
        devices = [
            {"name": "foreign", "udid": "F", "state": "Booted", "runtime": "ios"},
            {"name": "resting", "udid": "R", "state": "Shutdown", "runtime": "ios"},
        ]
        with mock.patch.object(sim_device, "_devices", return_value=devices), \
             mock.patch.object(sim_device, "_simctl") as simctl:
            self.assertEqual(sim_device.check_device_conflicts(), ["foreign (F)"])
        simctl.assert_not_called()

    def test_malformed_ownership_journal_fails_closed(self):
        with open(self.ownership, "w") as stream:
            json.dump([], stream)
        with self.assertRaisesRegex(sim_device.DeviceError, "unsupported schema"):
            sim_device._load_owned_clones()

    def test_deleted_owned_clone_is_forgotten(self):
        sim_device._record_clone("owned-name", "OWNED")
        device = {"name": "owned-name", "udid": "OWNED", "state": "Shutdown", "runtime": "ios"}
        golden = {"name": "golden", "udid": "GOLDEN", "state": "Shutdown", "runtime": "ios"}
        with mock.patch.object(sim_device, "_devices", side_effect=[[device], []]), \
             mock.patch.object(sim_device, "golden_info", return_value=golden), \
             mock.patch.object(sim_device, "_simctl") as simctl:
            simctl.return_value.returncode = 0
            self.assertTrue(sim_device.destroy("OWNED"))
        self.assertEqual(sim_device._load_owned_clones(), {})


if __name__ == "__main__":
    unittest.main()
