import os
import json
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench_env


class AgentDeviceShimTests(unittest.TestCase):
    def test_sandbox_blocks_raw_xcrun_lifecycle_bypass(self):
        completed = mock.Mock(
            returncode=0,
            stdout="/Applications/Xcode.app/Contents/Developer/usr/bin/simctl\n",
        )
        with mock.patch.object(bench_env.os.path, "exists", return_value=True), \
             mock.patch.object(bench_env, "device_set", return_value="/devices"), \
             mock.patch.object(bench_env.subprocess, "run", return_value=completed):
            wrapped = bench_env.sandbox_wrap(["opencode"], "CLONE")

        profile = wrapped[2]
        self.assertIn('(deny process-exec (literal "/usr/bin/xcrun"))', profile)
        self.assertIn('(deny file-read-data (literal "/usr/bin/xcrun"))', profile)
        self.assertIn(
            '(deny process-exec (literal '
            '"/Applications/Xcode.app/Contents/Developer/usr/bin/simctl"))',
            profile,
        )
        self.assertIn(
            '(deny file-write* (subpath %s))'
            % json.dumps(bench_env.simulator_state_dir()),
            profile,
        )

    def test_each_run_pins_an_independent_agent_device_executable(self):
        with tempfile.TemporaryDirectory() as root:
            first_run = os.path.join(root, "first")
            second_run = os.path.join(root, "second")
            os.makedirs(first_run)
            os.makedirs(second_run)

            with mock.patch.dict(
                os.environ,
                {"BENCH_NODE": "/opt/node-a", "BENCH_AGENT_DEVICE": "/opt/agent-device-a.mjs"},
            ):
                first_shim_dir = bench_env.agent_device_shim_dir(first_run)

            with mock.patch.dict(
                os.environ,
                {"BENCH_NODE": "/opt/node-b", "BENCH_AGENT_DEVICE": "/opt/agent-device-b.mjs"},
            ):
                second_shim_dir = bench_env.agent_device_shim_dir(second_run)

            first_shim = os.path.join(first_shim_dir, "agent-device")
            second_shim = os.path.join(second_shim_dir, "agent-device")
            with open(first_shim) as f:
                self.assertEqual(
                    f.read(),
                    '#!/bin/sh\nexec /opt/node-a /opt/agent-device-a.mjs "$@"\n',
                )
            with open(second_shim) as f:
                self.assertEqual(
                    f.read(),
                    '#!/bin/sh\nexec /opt/node-b /opt/agent-device-b.mjs "$@"\n',
                )
            self.assertNotEqual(first_shim, second_shim)

    def test_a_run_shim_cannot_be_retargeted_after_creation(self):
        with tempfile.TemporaryDirectory() as run_dir:
            with mock.patch.dict(
                os.environ,
                {"BENCH_NODE": "/opt/node-a", "BENCH_AGENT_DEVICE": "/opt/agent-device-a.mjs"},
            ):
                bench_env.agent_device_shim_dir(run_dir)

            with mock.patch.dict(
                os.environ,
                {"BENCH_NODE": "/opt/node-b", "BENCH_AGENT_DEVICE": "/opt/agent-device-b.mjs"},
            ):
                with self.assertRaisesRegex(RuntimeError, "already pins a different executable"):
                    bench_env.agent_device_shim_dir(run_dir)

    def test_shell_startup_keeps_the_run_shim_ahead_of_user_path_changes(self):
        with tempfile.TemporaryDirectory() as run_dir:
            with mock.patch.dict(
                os.environ,
                {"BENCH_NODE": "/opt/node-a", "BENCH_AGENT_DEVICE": "/opt/agent-device-a.mjs"},
            ):
                shell_env = bench_env.agent_device_shell_env(run_dir, "/usr/bin:/bin")

            shim_dir = os.path.join(run_dir, "shell-env", "bin")
            startup = os.path.join(run_dir, "shell-env", "agent-device-path.sh")
            self.assertEqual(shell_env["PATH"], f"{shim_dir}:/usr/bin:/bin")
            self.assertEqual(shell_env["ZDOTDIR"], os.path.join(run_dir, "shell-env"))
            self.assertEqual(shell_env["BASH_ENV"], startup)
            self.assertEqual(shell_env["ENV"], startup)
            with open(os.path.join(run_dir, "shell-env", ".zshenv")) as f:
                self.assertEqual(f.read(), f'export PATH="{shim_dir}:$PATH"\n')
            with open(startup) as f:
                self.assertEqual(f.read(), f'export PATH="{shim_dir}:$PATH"\n')


if __name__ == "__main__":
    unittest.main()
