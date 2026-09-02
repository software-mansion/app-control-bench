#!/usr/bin/env python3
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doctor


class ToolSetupTests(unittest.TestCase):
    def test_every_tool_has_an_explicit_setup_policy(self):
        self.assertEqual(set(doctor.bench.TOOLS), set(doctor.TOOL_SETUP))

    def test_argent_and_none_do_not_run_agent_device_doctor(self):
        with mock.patch.object(doctor, "_run_agent_device_doctor") as run_doctor:
            self.assertEqual(doctor.setup_tools(["argent", "none"], {}), 0)
        run_doctor.assert_not_called()

    def test_agent_device_waits_for_artifact_without_starting_a_task_runtime(self):
        warming = {
            "success": True,
            "data": {"checks": [{"id": "ios-runner-cache", "status": "pass", "hint": "wait"}]},
        }
        ready = {
            "success": True,
            "data": {"checks": [{"id": "ios-runner-cache", "status": "pass"}]},
        }
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(
            doctor, "_run_agent_device_doctor", side_effect=[(completed, warming), (completed, ready)]
        ) as run_doctor, mock.patch.object(doctor, "_stop_agent_device_setup_daemon") as stop_daemon, \
             mock.patch.object(doctor.time, "sleep"):
            failures = doctor.setup_tools(["agent-device"], {"agent_device": "/tool"})
        self.assertEqual(failures, 0)
        self.assertEqual(run_doctor.call_count, 2)
        state_dirs = {call.args[1] for call in run_doctor.call_args_list}
        self.assertEqual(len(state_dirs), 1)
        self.assertTrue(next(iter(state_dirs)).startswith("/"))
        stop_daemon.assert_called_once_with("/tool", next(iter(state_dirs)))


if __name__ == "__main__":
    unittest.main()
