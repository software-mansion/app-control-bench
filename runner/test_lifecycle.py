#!/usr/bin/env python3
"""Local unit tests for the harness's lifecycle plumbing - run_all.sh's doctor calls, agent-device
package resolution, the simctl device set, and golden lookup. No devices, no network, no API spend:
simctl and python3 are faked, and every tree lives under tempfile.

    python3 runner/test_lifecycle.py
"""
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import bench
import bench_env
import sim_device

DEVICE_SET = "/tmp/bench device set"   # the space proves the shell-string callers quote it


def fake_run(stdout=""):
    return mock.Mock(return_value=subprocess.CompletedProcess([], 0, stdout, ""))


class RunAllTests(unittest.TestCase):
    def test_tools_reaches_every_doctor_call(self):
        with tempfile.TemporaryDirectory() as d:
            log = os.path.join(d, "argv.log")
            stub = os.path.join(d, "python3")
            with open(stub, "w") as f:
                f.write('#!/bin/sh\necho "$*" >> "$ARGV_LOG"\n')
            os.chmod(stub, 0o755)
            env = {k: v for k, v in os.environ.items() if k not in ("ONLY", "APPS", "FORCE")}
            env.update(PATH=d + os.pathsep + env["PATH"], ARGV_LOG=log, TOOLS="argent", SKIP_JUDGE="1")
            subprocess.run([os.path.join(os.path.dirname(HERE), "run_all.sh")], env=env,
                           capture_output=True, check=True)
            with open(log) as f:
                calls = f.read().splitlines()
        self.assertEqual(calls, [
            "runner/doctor.py --preflight --tools argent",
            "runner/doctor.py --heal --tools argent",
            "runner/run_matrix.py",
            "runner/doctor.py --tools argent",
        ])


class AgentDeviceDirTests(unittest.TestCase):
    """A global npm install: <prefix>/bin/agent-device is a symlink into <prefix>/lib/node_modules."""

    def install(self, entry):
        """Fake agent-device package whose linked binary is `entry`; returns the package's realpath."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pkg = os.path.join(tmp.name, "lib", "node_modules", "agent-device")
        os.makedirs(os.path.join(pkg, os.path.dirname(entry)))
        with open(os.path.join(pkg, "package.json"), "w") as f:
            json.dump({"name": "agent-device", "version": "9.9.9"}, f)
        open(os.path.join(pkg, entry), "w").close()
        os.makedirs(os.path.join(tmp.name, "bin"))
        link = os.path.join(tmp.name, "bin", "agent-device")
        os.symlink(os.path.join(pkg, entry), link)
        env = mock.patch.dict(os.environ, {"BENCH_AGENT_DEVICE": link})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("BENCH_AGENT_DEVICE_DIR", None)
        return os.path.realpath(pkg)

    def test_symlinked_binary_resolves_to_the_package(self):
        pkg = self.install("bin/agent-device.mjs")
        self.assertEqual(bench_env.agent_device_dir(), pkg)
        self.assertEqual(bench_env.agent_device_skills_dir(), os.path.join(pkg, "skills"))

    def test_entry_below_bin_still_resolves_to_the_package(self):
        pkg = self.install("dist/src/bin.js")
        self.assertEqual(bench_env.agent_device_dir(), pkg)

    def test_symlinked_binary_lands_in_its_versioned_cell(self):
        self.install("bin/agent-device.mjs")
        with mock.patch.object(bench, "ADEV_DIR", bench_env.agent_device_dir()), \
             mock.patch.dict(bench._variant_cache, clear=True):
            self.assertEqual(bench.tool_variant("agent-device"), "agent-device@9.9.9")


class DeviceSetTests(unittest.TestCase):
    def setUp(self):
        env = mock.patch.dict(os.environ, {"BENCH_DEVICE_SET": DEVICE_SET})
        env.start()
        self.addCleanup(env.stop)

    def test_booted_udids_lists_the_bench_set(self):
        udid = "11111111-2222-3333-4444-555555555555"
        run = fake_run(f"    bench-run-1-bsky-01 ({udid}) (Booted)\n")
        with mock.patch.object(subprocess, "run", run):
            self.assertEqual(bench_env.booted_udids(), [udid])
        self.assertEqual(run.call_args.args[0],
                         ["xcrun", "simctl", "--set", DEVICE_SET, "list", "devices", "booted"])

    def test_sim_device_simctl_targets_the_bench_set(self):
        run = fake_run()
        with mock.patch.object(subprocess, "run", run):
            sim_device._simctl("delete", "UDID")
        self.assertEqual(run.call_args.args[0], ["xcrun", "simctl", "--set", DEVICE_SET, "delete", "UDID"])

    def test_app_launch_and_screenshot_target_the_bench_set(self):
        run = fake_run()
        apps = {"plain": {"bundle": "com.example.plain", "load_wait": 0},
                "linked": {"bundle": "com.example.linked", "open": "example://home", "load_wait": 0}}
        with mock.patch.object(subprocess, "run", run), mock.patch.object(bench.time, "sleep"), \
             mock.patch.dict(bench.APPS, apps):
            bench.reset_app("plain", "UDID")
            bench.reset_app("linked", "UDID")
            bench.screenshot("UDID", "/tmp/shot dir/final.png")
        argvs = [shlex.split(c.args[0]) for c in run.call_args_list]
        prefix = ["xcrun", "simctl", "--set", DEVICE_SET]
        self.assertEqual(argvs, [
            prefix + ["get_app_container", "UDID", "com.example.plain"],
            prefix + ["terminate", "UDID", "com.example.plain", "2>/dev/null"],
            prefix + ["launch", "UDID", "com.example.plain"],
            prefix + ["get_app_container", "UDID", "com.example.linked"],
            prefix + ["terminate", "UDID", "com.example.linked", "2>/dev/null"],
            prefix + ["openurl", "UDID", "example://home"],
            prefix + ["io", "UDID", "screenshot", "/tmp/shot dir/final.png"],
        ])


class GoldenTests(unittest.TestCase):
    def test_a_golden_name_on_two_simulators_is_refused(self):
        def device(udid):
            return {"name": "bench-golden-v1", "udid": udid, "state": "Shutdown", "isAvailable": True}
        listing = {"devices": {"com.apple.CoreSimulator.SimRuntime.iOS-18-5": [device("AAAA")],
                               "com.apple.CoreSimulator.SimRuntime.iOS-26-0": [device("BBBB")]}}
        with mock.patch.object(sim_device, "load_manifest", return_value={"name": "bench-golden-v1"}), \
             mock.patch.object(subprocess, "run", fake_run(json.dumps(listing))):
            with self.assertRaisesRegex(sim_device.DeviceError, r"carried by 2 simulators \(AAAA, BBBB\)"):
                sim_device.check_golden()


if __name__ == "__main__":
    unittest.main()
