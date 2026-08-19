#!/usr/bin/env python3
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_env
import gen_configs
import isolation
import ledger
import sim_device
import bench


class ProvenanceTests(unittest.TestCase):
    def test_run_config_stages_first_skill_when_install_roots_share_a_name(self):
        with tempfile.TemporaryDirectory() as d:
            configs = os.path.join(d, "configs")
            os.makedirs(os.path.join(configs, "agent-device"))
            with open(os.path.join(configs, "agent-device", "opencode.json"), "w") as f:
                json.dump({"permission": {}, "mcp": {}}, f)

            skill_roots = []
            for root_name, body in (("repo", "source checkout"), ("user", "installed copy")):
                skill = os.path.join(d, root_name, "agent-device")
                os.makedirs(skill)
                with open(os.path.join(skill, "SKILL.md"), "w") as f:
                    f.write(body)
                skill_roots.append(skill)

            with mock.patch.object(
                gen_configs, "_skill_sources", return_value=(skill_roots, None, None)
            ):
                run_config = gen_configs.write_run_config(
                    "agent-device", "SIMULATOR", configs_root=configs
                )
                manifest = gen_configs.skill_manifest("agent-device")
                self.addCleanup(lambda: shutil.rmtree(run_config, ignore_errors=True))
                staged = os.path.join(run_config, ".opencode", "skills", "agent-device", "SKILL.md")
                with open(staged) as f:
                    self.assertEqual(f.read(), "source checkout")
                self.assertEqual(list(manifest), ["agent-device"])
                with open(os.path.join(run_config, "opencode.json")) as f:
                    permission = json.load(f)["permission"]
                self.assertEqual(permission["external_directory"], "deny")
                self.assertEqual(
                    permission["bash"], {"*": "deny", "agent-device *": "allow"}
                )

    def test_no_tool_v4_config_has_no_mutation_shell(self):
        with tempfile.TemporaryDirectory() as d:
            configs = os.path.join(d, "configs")
            os.makedirs(os.path.join(configs, "none"))
            with open(os.path.join(configs, "none", "opencode.json"), "w") as stream:
                json.dump({"permission": {}, "mcp": {}}, stream)

            run_config = gen_configs.write_run_config("none", "CLONE", configs_root=configs)
            self.addCleanup(lambda: shutil.rmtree(run_config, ignore_errors=True))
            with open(os.path.join(run_config, "opencode.json")) as stream:
                permission = json.load(stream)["permission"]

        self.assertEqual(permission["bash"], "deny")
        self.assertEqual(permission["external_directory"], "deny")

    def test_global_npm_symlink_resolves_package_root(self):
        with tempfile.TemporaryDirectory() as d:
            package = os.path.join(d, "lib", "node_modules", "agent-device")
            os.makedirs(os.path.join(package, "bin"))
            with open(os.path.join(package, "package.json"), "w") as f:
                json.dump({"name": "agent-device", "version": "0.20.8"}, f)
            target = os.path.join(package, "bin", "agent-device.mjs")
            open(target, "w").close()
            os.makedirs(os.path.join(d, "bin"))
            link = os.path.join(d, "bin", "agent-device")
            os.symlink(target, link)
            with mock.patch.dict(os.environ, {"BENCH_AGENT_DEVICE": link}, clear=False):
                os.environ.pop("BENCH_AGENT_DEVICE_DIR", None)
                self.assertEqual(bench_env.agent_device_dir(), os.path.realpath(package))

    def test_version_or_skill_change_makes_result_runnable(self):
        with tempfile.TemporaryDirectory() as d:
            out = ledger.result_dir(d, "gpt_low", "agent-device", "bsky-01")
            os.makedirs(out)
            open(os.path.join(out, "final.png"), "wb").close()
            with open(os.path.join(out, "meta.json"), "w") as f:
                json.dump({"returncode": 0, "versions": {"harness": "h", "tool": "0.17.6",
                                                          "skill_hashes": {"agent-device": "old"}}}, f)
            self.assertTrue(ledger.needs_run(d, "gpt_low", "agent-device", "bsky-01", "h",
                                             {"tool": "0.20.8", "skill_hashes": {"agent-device": "new"}}))

    def test_skill_manifest_includes_references(self):
        with tempfile.TemporaryDirectory() as d:
            skill = os.path.join(d, "agent-device")
            os.makedirs(os.path.join(skill, "references"))
            with open(os.path.join(skill, "SKILL.md"), "w") as f:
                f.write("router")
            ref = os.path.join(skill, "references", "workflow.md")
            with open(ref, "w") as f:
                f.write("v1")
            with mock.patch.object(gen_configs, "_skill_sources", return_value=([skill], None, None)):
                before = gen_configs.skill_manifest("agent-device")
                with open(ref, "w") as f:
                    f.write("v2")
                after = gen_configs.skill_manifest("agent-device")
            self.assertNotEqual(before, after)


class SharedMachineSafetyTests(unittest.TestCase):
    def setUp(self):
        self.ownership_dir = tempfile.TemporaryDirectory()
        self.ownership_patch = mock.patch.object(
            sim_device,
            "OWNERSHIP_FILE",
            os.path.join(self.ownership_dir.name, "owned-clones.json"),
        )
        self.ownership_patch.start()

    def tearDown(self):
        self.ownership_patch.stop()
        self.ownership_dir.cleanup()

    def test_conflict_check_never_mutates_simulators(self):
        devices = [
            {"name": "Personal iPhone", "udid": "P", "state": "Shutdown", "runtime": "iOS"},
            {"name": "Already Booted", "udid": "B", "state": "Booted", "runtime": "iOS"},
        ]
        with mock.patch.object(sim_device, "_devices", return_value=devices), \
             mock.patch.object(sim_device, "_simctl") as simctl:
            self.assertEqual(sim_device.check_device_conflicts(), ["Already Booted (B)"])
            simctl.assert_not_called()

    def test_golden_lookup_rejects_duplicate_names(self):
        devices = [
            {"name": "bench-golden-v4", "udid": "A", "state": "Shutdown", "runtime": "iOS"},
            {"name": "bench-golden-v4", "udid": "B", "state": "Shutdown", "runtime": "iOS"},
        ]
        with mock.patch.object(sim_device, "load_manifest", return_value={"name": "bench-golden-v4"}), \
             mock.patch.object(sim_device, "_devices", return_value=devices):
            with self.assertRaisesRegex(sim_device.DeviceError, "ambiguous.*A.*B"):
                sim_device.golden_info()

    def test_owned_clone_is_shut_down_and_deleted(self):
        sim_device._record_clone("bench-run-1-bsky-01", "B")
        shutdown = mock.Mock(returncode=0, stderr="")
        deleted = mock.Mock(returncode=0, stderr="")
        devices = [
            [{"name": "bench-run-1-bsky-01", "udid": "B", "state": "Shutdown", "runtime": "iOS"}],
            [{"name": "bench-run-1-bsky-01", "udid": "B", "state": "Shutdown", "runtime": "iOS"}],
            [],
        ]
        with mock.patch.object(sim_device, "_simctl", side_effect=[shutdown, deleted]) as simctl, \
             mock.patch.object(sim_device, "_devices", side_effect=devices), \
             mock.patch.object(sim_device, "golden_info", return_value={"udid": "G", "name": "golden"}):
            self.assertTrue(sim_device.destroy("B"))

        self.assertEqual(
            simctl.call_args_list,
            [mock.call("shutdown", "B", timeout=60), mock.call("delete", "B", timeout=60)],
        )
        self.assertEqual(sim_device._load_owned_clones(), {})

    def test_clone_intent_is_durable_before_simctl_can_create_a_device(self):
        udid = "11111111-2222-3333-4444-555555555555"
        created = mock.Mock(returncode=0, stdout=f"{udid}\n", stderr="")

        def create(*args, **kwargs):
            journal = sim_device._load_owned_clones()
            self.assertEqual(len(journal), 1)
            self.assertIsNone(next(iter(journal.values()))["udid"])
            return created

        with mock.patch.object(
            sim_device, "check_golden", return_value={"udid": "G", "name": "golden"}
        ), mock.patch.object(sim_device, "_simctl", side_effect=create):
            self.assertEqual(sim_device.clone_golden("bsky-01"), udid)

        record = next(iter(sim_device._load_owned_clones().values()))
        self.assertEqual(record["udid"], udid)
        self.assertTrue(record["name"].startswith("bench-run-"))

    def test_unbound_intent_blocks_next_run_when_clone_is_not_yet_visible(self):
        sim_device._record_clone("bench-run-delayed")
        with mock.patch.object(sim_device, "_devices", return_value=[]), \
             mock.patch.object(sim_device.time, "time", return_value=10**12):
            with self.assertRaisesRegex(sim_device.DeviceError, "still unresolved; refusing"):
                sim_device.reap_owned_clones()

        self.assertIn("bench-run-delayed", sim_device._load_owned_clones())

    def test_timed_out_clone_retires_exact_materialized_device_before_raising(self):
        udid = "11111111-2222-3333-4444-555555555555"
        materialized = {
            "name": None,
            "udid": udid,
            "state": "Shutdown",
            "runtime": "iOS",
        }

        def devices():
            name = next(iter(sim_device._load_owned_clones()))
            return [{**materialized, "name": name}]

        with mock.patch.object(
            sim_device, "check_golden", return_value={"udid": "G", "name": "golden"}
        ), mock.patch.object(
            sim_device, "_simctl", side_effect=sim_device.DeviceError("simctl clone timed out")
        ), mock.patch.object(sim_device, "_devices", side_effect=devices), \
             mock.patch.object(sim_device, "destroy", return_value=True) as destroy:
            with self.assertRaisesRegex(sim_device.DeviceError, "simctl clone timed out"):
                sim_device.clone_golden("bsky-01")

        destroy.assert_called_once_with(udid)

    def test_failed_clone_retires_exact_materialized_device_before_raising(self):
        udid = "11111111-2222-3333-4444-555555555555"
        failed = mock.Mock(returncode=1, stdout="", stderr="clone failed")
        materialized = {
            "name": None,
            "udid": udid,
            "state": "Shutdown",
            "runtime": "iOS",
        }

        def devices():
            name = next(iter(sim_device._load_owned_clones()))
            return [{**materialized, "name": name}]

        with mock.patch.object(
            sim_device, "check_golden", return_value={"udid": "G", "name": "golden"}
        ), mock.patch.object(sim_device, "_simctl", return_value=failed), \
             mock.patch.object(sim_device, "_devices", side_effect=devices), \
             mock.patch.object(sim_device, "destroy", return_value=True) as destroy:
            with self.assertRaisesRegex(sim_device.DeviceError, "clone of 'golden' failed"):
                sim_device.clone_golden("bsky-01")

        destroy.assert_called_once_with(udid)

    def test_corrupt_ownership_journal_fails_closed(self):
        os.makedirs(os.path.dirname(sim_device.OWNERSHIP_FILE), exist_ok=True)
        with open(sim_device.OWNERSHIP_FILE, "w") as stream:
            stream.write("not json")
        with mock.patch.object(sim_device, "_simctl") as simctl:
            with self.assertRaisesRegex(sim_device.DeviceError, "ownership journal is unreadable"):
                sim_device.reap_owned_clones()
        simctl.assert_not_called()

    def test_reaps_only_durably_journaled_clones_after_process_restart(self):
        sim_device._record_clone("bench-run-owned", "B")
        shutdown = mock.Mock(returncode=0, stderr="")
        deleted = mock.Mock(returncode=0, stderr="")
        devices = [
            [
                {"name": "Personal iPhone", "udid": "P", "state": "Shutdown", "runtime": "iOS"},
                {"name": "bench-run-owned", "udid": "B", "state": "Shutdown", "runtime": "iOS"},
                {"name": "bench-run-unowned", "udid": "U", "state": "Shutdown", "runtime": "iOS"},
            ],
            [{"name": "bench-run-owned", "udid": "B", "state": "Shutdown", "runtime": "iOS"}],
            [],
        ]
        with mock.patch.object(sim_device, "_devices", side_effect=devices), \
             mock.patch.object(sim_device, "_simctl", side_effect=[shutdown, deleted]) as simctl, \
             mock.patch.object(sim_device, "golden_info", return_value={"udid": "G", "name": "golden"}):
            self.assertEqual(sim_device.reap_owned_clones(), ["bench-run-owned"])

        self.assertEqual(
            simctl.call_args_list,
            [mock.call("shutdown", "B", timeout=60), mock.call("delete", "B", timeout=60)],
        )
        self.assertEqual(sim_device._load_owned_clones(), {})

    def test_destroy_refuses_foreign_and_golden_simulators(self):
        with mock.patch.object(sim_device, "_simctl") as simctl, \
             mock.patch.object(sim_device, "golden_info", return_value={"udid": "G", "name": "golden"}):
            self.assertFalse(sim_device.destroy("FOREIGN"))
            self.assertFalse(sim_device.destroy("G"))
        simctl.assert_not_called()

    def test_boot_failure_still_retires_the_clone_without_invoking_model(self):
        task = {"id": "bsky-01", "app": "bluesky", "kind": "nav", "needs_auth": True,
                "prompt": "Open feeds"}
        with tempfile.TemporaryDirectory() as results, \
             mock.patch.object(bench, "RESULTS", results), \
             mock.patch.object(bench.ledger, "needs_run", return_value=True), \
             mock.patch.object(bench.ledger, "reset"), \
             mock.patch.object(bench, "ollama_served_cached", return_value=set()), \
             mock.patch.object(bench, "run_versions", return_value={}), \
             mock.patch.object(bench.isolation, "teardown_all", return_value={}), \
             mock.patch.object(bench.sim_device, "check_device_conflicts", return_value=[]), \
             mock.patch.object(bench.sim_device, "reap_owned_clones", return_value=[]), \
             mock.patch.object(bench.sim_device, "check_golden", return_value={"name": "golden", "udid": "G"}), \
             mock.patch.object(bench.bluesky_control, "backend_identity", return_value={
                 "pds_url": "pds", "appview_did": "appview", "bench_did": "bench"
             }), \
             mock.patch.object(bench.sim_device, "require_bluesky_backend_identity"), \
             mock.patch.object(bench, "reset_hook", return_value={}), \
             mock.patch.object(bench.sim_device, "refresh_golden_sessions", return_value=False), \
             mock.patch.object(bench.sim_device, "clone_golden", return_value="B"), \
             mock.patch.object(bench.sim_device, "boot", side_effect=sim_device.DeviceError("boot failed")), \
             mock.patch.object(bench.sim_device, "destroy", return_value=True) as destroy, \
             mock.patch.object(bench.isolation, "run_opencode") as paid_model:
            result = bench.run_one("gpt_low", "agent-device", task, force=True)

        self.assertEqual(result["returncode"], -2)
        destroy.assert_called_once_with("B")
        paid_model.assert_not_called()

    def test_verify_golden_fails_if_its_clone_cannot_be_retired(self):
        with mock.patch.object(bench.isolation, "teardown_all"), \
             mock.patch.object(bench.sim_device, "check_device_conflicts", return_value=[]), \
             mock.patch.object(
                 bench.sim_device,
                 "check_golden",
                 return_value={"name": "golden", "udid": "G"},
             ), mock.patch.object(bench.sim_device, "reap_owned_clones", return_value=[]), \
             mock.patch.object(bench.sim_device, "clone_golden", return_value="B"), \
             mock.patch.object(bench.sim_device, "boot"), \
             mock.patch.object(bench.sim_device, "destroy", return_value=False), \
             mock.patch.object(bench, "APPS", {}):
            with self.assertRaisesRegex(sim_device.DeviceError, "verification clone B"):
                bench.verify_golden()

    def test_close_uses_only_the_per_run_daemon_state(self):
        with mock.patch.object(isolation, "KILL_PATTERNS", []), \
             mock.patch.object(isolation, "_owned_pids", return_value=set()), \
             mock.patch.object(isolation.subprocess, "run") as run:
            isolation.teardown_all(
                "post", adev="agent-device", adev_udid="B", adev_state_dir="/tmp/bench-state")
            self.assertEqual(run.call_args.kwargs["env"]["AGENT_DEVICE_STATE_DIR"], "/tmp/bench-state")


if __name__ == "__main__":
    unittest.main()
