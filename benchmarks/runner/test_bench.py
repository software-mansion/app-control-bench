import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench
import bluesky_control


class AgentCommandIsolationTests(unittest.TestCase):
    def test_unattended_agent_command_honors_explicit_permission_denials(self):
        with mock.patch.object(bench, "OPENCODE", "/bin/opencode"):
            command = bench.agent_command("gpt_low", "do the task")

        self.assertIn("--auto", command)
        self.assertNotIn("--dangerously-skip-permissions", command)

    def test_cli_agents_cannot_read_historical_results(self):
        with mock.patch.object(bench.bench_env, "sandbox_wrap", return_value=["sandboxed"]) as wrap:
            command = bench.sandbox_agent_command(["opencode", "run"], "agent-device", "UDID")

        self.assertEqual(command, ["sandboxed"])
        wrap.assert_called_once_with(
            ["opencode", "run"], udid="UDID", denied_read_paths=[bench.RESULTS]
        )

    def test_starts_clone_scoped_daemon_before_restricted_model_shell(self):
        completed = mock.Mock(returncode=0, stderr="")
        with mock.patch.object(bench, "ADEV", "/bin/agent-device"), \
             mock.patch.object(bench.subprocess, "run", return_value=completed) as run:
            bench.start_clone_scoped_agent_device_daemon("/run/device.json", "/run/state")

        command = run.call_args.args[0]
        child_env = run.call_args.kwargs["env"]
        self.assertEqual(command, ["/bin/agent-device", "devices", "--platform", "ios", "--json"])
        self.assertEqual(child_env["AGENT_DEVICE_CONFIG"], "/run/device.json")
        self.assertEqual(child_env["AGENT_DEVICE_STATE_DIR"], "/run/state")

    def test_retry_restarts_trusted_daemon_after_teardown(self):
        order = []
        with mock.patch.object(
            bench.isolation,
            "teardown_all",
            side_effect=lambda *args, **kwargs: order.append(("teardown", args, kwargs)),
        ), mock.patch.object(
            bench,
            "start_clone_scoped_agent_device_daemon",
            side_effect=lambda *args: order.append(("start", args)),
        ):
            bench.reset_agent_services_for_retry(
                "agent-device", "CLONE", "/run/state", "/run/device.json"
            )

        self.assertEqual(order[0][0], "teardown")
        self.assertEqual(order[0][2]["adev_udid"], "CLONE")
        self.assertEqual(order[0][2]["adev_state_dir"], "/run/state")
        self.assertEqual(order[1], ("start", ("/run/device.json", "/run/state")))

    def test_tool_less_control_runs_once_when_transcript_has_no_tool_calls(self):
        task = {
            "id": "element-01",
            "app": "element",
            "kind": "nav",
            "needs_auth": False,
            "prompt": "Open the room",
        }
        with tempfile.TemporaryDirectory() as root:
            cfg_dir = os.path.join(root, "cfg")
            os.makedirs(cfg_dir)
            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(bench, "RESULTS", os.path.join(root, "results")))
                stack.enter_context(mock.patch.object(bench.ledger, "needs_run", return_value=True))
                stack.enter_context(mock.patch.object(bench.ledger, "reset"))
                stack.enter_context(mock.patch.object(bench.ledger, "mark"))
                stack.enter_context(mock.patch.object(bench, "run_versions", return_value={}))
                stack.enter_context(mock.patch.object(bench, "ollama_served_cached", return_value=set()))
                stack.enter_context(mock.patch.object(bench.isolation, "teardown_all", return_value={}))
                stack.enter_context(mock.patch.object(bench.isolation, "write_env_manifest"))
                stack.enter_context(mock.patch.object(bench.isolation, "start_proxies", return_value={}))
                run_model = stack.enter_context(
                    mock.patch.object(bench.isolation, "run_opencode", return_value=(0, "", False))
                )
                stack.enter_context(mock.patch.object(bench.sim_device, "check_device_conflicts", return_value=[]))
                stack.enter_context(mock.patch.object(bench.sim_device, "reap_owned_clones", return_value=[]))
                stack.enter_context(mock.patch.object(
                    bench.sim_device, "check_golden", return_value={"name": "golden", "udid": "G"}
                ))
                stack.enter_context(mock.patch.object(bench.sim_device, "clone_golden", return_value="CLONE"))
                stack.enter_context(mock.patch.object(bench.sim_device, "boot"))
                stack.enter_context(mock.patch.object(bench.sim_device, "destroy", return_value=True))
                stack.enter_context(mock.patch.object(bench.gen_configs, "write_run_config", return_value=cfg_dir))
                stack.enter_context(mock.patch.object(bench, "reset_hook", return_value={}))
                stack.enter_context(mock.patch.object(bench, "reset_app"))
                stack.enter_context(mock.patch.object(bench, "screenshot"))
                stack.enter_context(mock.patch.object(bench, "parse_transcript", return_value=(0, [])))
                reset_retry = stack.enter_context(
                    mock.patch.object(bench, "reset_agent_services_for_retry")
                )

                result = bench.run_one("gpt_low", "none", task, force=True)

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["n_tool_calls"], 0)
        run_model.assert_called_once()
        reset_retry.assert_not_called()


class CompletionContractTests(unittest.TestCase):
    def test_reports_only_units_that_do_not_match_current_provenance(self):
        tasks = [
            {"id": "bsky-01", "app": "bluesky"},
            {"id": "bsky-02", "app": "bluesky"},
        ]
        with mock.patch.object(bench, "run_versions", return_value={"tool": "0.20.10"}), \
             mock.patch.object(bench, "model_route", return_value="route"), \
             mock.patch.object(bench.ledger, "needs_run", side_effect=[False, True]):
            missing = bench.incomplete_units([("gpt_low", "agent-device")], tasks)

        self.assertEqual(missing, ["gpt_low:agent-device/bsky-02"])


class BlueskyPaidRunGuardTests(unittest.TestCase):
    def test_backend_and_actual_clone_auth_are_verified_before_model_invocation(self):
        task = {"id": "bsky-25", "app": "bluesky", "kind": "interact", "needs_auth": True,
                "prompt": "Reply nice one"}
        identity = {"pds_url": "http://localhost:3000", "appview_did": "did:web:appview",
                    "bench_did": "did:plc:bench"}
        order = []
        with tempfile.TemporaryDirectory() as root:
            cfg_dir = os.path.join(root, "cfg")
            os.makedirs(cfg_dir)

            def run_model(*args, **kwargs):
                order.append("model")
                return 0, "", False

            def reset_hook(app, phase, *args):
                if phase == "post":
                    order.append("reset_post")
                return {"phase": phase}

            with ExitStack() as stack:
                stack.enter_context(mock.patch.object(bench, "RESULTS", os.path.join(root, "results")))
                stack.enter_context(mock.patch.object(bench.ledger, "needs_run", return_value=True))
                stack.enter_context(mock.patch.object(bench.ledger, "reset"))
                stack.enter_context(mock.patch.object(bench.ledger, "mark"))
                stack.enter_context(mock.patch.object(bench, "run_versions", return_value={}))
                stack.enter_context(mock.patch.object(bench, "ollama_served_cached", return_value=set()))
                stack.enter_context(mock.patch.object(
                    bench.bench_env,
                    "agent_device_shell_env",
                    return_value={"PATH": root, "ZDOTDIR": root, "BASH_ENV": root, "ENV": root},
                ))
                stack.enter_context(mock.patch.object(bench.isolation, "teardown_all", return_value={}))
                stack.enter_context(mock.patch.object(bench.isolation, "write_env_manifest"))
                stack.enter_context(mock.patch.object(bench.isolation, "start_proxies", return_value={}))
                stack.enter_context(
                    mock.patch.object(bench, "start_clone_scoped_agent_device_daemon")
                )
                stack.enter_context(mock.patch.object(bench.isolation, "run_opencode", side_effect=run_model))
                stack.enter_context(mock.patch.object(bench.sim_device, "check_device_conflicts", return_value=[]))
                stack.enter_context(mock.patch.object(bench.sim_device, "reap_owned_clones", return_value=[]))
                stack.enter_context(mock.patch.object(
                    bench.sim_device, "check_golden", return_value={"name": "golden", "udid": "G"}
                ))
                stack.enter_context(mock.patch.object(bench.sim_device, "clone_golden", return_value="CLONE"))
                stack.enter_context(mock.patch.object(bench.sim_device, "boot"))
                stack.enter_context(mock.patch.object(bench.sim_device, "destroy", return_value=True))
                stack.enter_context(mock.patch.object(bench.sim_device, "refresh_golden_sessions", return_value=True))
                stack.enter_context(mock.patch.object(
                    bench.sim_device, "require_bluesky_backend_identity",
                    side_effect=lambda value: order.append("identity")
                ))
                stack.enter_context(mock.patch.object(
                    bench.sim_device, "confirm_bluesky_clone_session",
                    side_effect=lambda *args, **kwargs: order.append("confirm")
                ))
                stack.enter_context(mock.patch.object(bluesky_control, "backend_identity", return_value=identity))
                stack.enter_context(mock.patch.object(
                    bluesky_control, "validate_clone_session",
                    side_effect=lambda *args, **kwargs: (
                        order.append("clone_auth") or {"authenticated": True}
                    )
                ))
                stack.enter_context(mock.patch.object(
                    bluesky_control,
                    "assert_mutation_postcondition",
                    side_effect=lambda task_id: (
                        order.append("postcondition")
                        or {"schema": "bluesky-postcondition/v1", "task": task_id, "passed": True}
                    ),
                ))
                stack.enter_context(mock.patch.object(bench.gen_configs, "write_run_config", return_value=cfg_dir))
                stack.enter_context(mock.patch.object(bench, "reset_hook", side_effect=reset_hook))
                stack.enter_context(mock.patch.object(bench, "reset_app"))
                stack.enter_context(mock.patch.object(bench, "screenshot"))
                stack.enter_context(mock.patch.object(bench, "parse_transcript", return_value=(1, ["shell"])))
                stack.enter_context(mock.patch.object(
                    bench, "sandbox_agent_command", side_effect=lambda command, *_: command
                ))
                result = bench.run_one("gpt_low", "agent-device", task, force=True)

        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["postcondition"]["task"], "bsky-25")
        self.assertEqual(
            order,
            ["identity", "clone_auth", "confirm", "model", "postcondition", "reset_post"],
        )


if __name__ == "__main__":
    unittest.main()
