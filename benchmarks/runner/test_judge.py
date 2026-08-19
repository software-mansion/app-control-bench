import os
import sys
import json
import tempfile
import unittest
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judge


@contextmanager
def fake_judge_proxy(_key):
    yield "http://127.0.0.1:48765/v1"


class OpenCodeOutputTests(unittest.TestCase):
    def test_extracts_text_events_only(self):
        stdout = "\n".join(
            [
                '{"type":"step_start","part":{}}',
                '{"type":"text","part":{"text":"{\\"verdict\\":\\"success\\"}"}}',
                '{"type":"step_finish","part":{"cost":0}}',
            ]
        )

        self.assertEqual(judge.parse_opencode_text(stdout), '{"verdict":"success"}')

    def test_provider_qualified_model_uses_opencode_in_auto_mode_even_with_api_key(self):
        with mock.patch.object(judge, "OAI_KEY", "test-key"), \
             mock.patch.object(judge, "call_vision_via_opencode", return_value="ok") as opencode:
            result = judge.call_vision(
                "prompt", "unused.png", "openai/gpt-5.6-luna", backend="auto"
            )

        self.assertEqual(result, "ok")
        opencode.assert_called_once_with(
            "prompt", "unused.png", "openai/gpt-5.6-luna", judge.JUDGE_VARIANT
        )

    def test_luna_uses_dedicated_active_proxy_with_placeholder_credentials(self):
        captured = {}

        def run_opencode(command, **kwargs):
            captured["cwd"] = kwargs.get("cwd")
            child_env = kwargs.get("env") or {}
            captured["provider_env"] = {
                key: child_env.get(key)
                for key in ("OPENAI_API_KEY", "AI_GATEWAY_API_KEY")
            }
            config_path = child_env.get("OPENCODE_CONFIG")
            if config_path:
                with open(config_path) as stream:
                    captured["config"] = json.load(stream)
            else:
                captured["config"] = None
            auth_path = os.path.join(
                child_env.get("XDG_DATA_HOME", ""), "opencode", "auth.json"
            )
            if os.path.exists(auth_path):
                with open(auth_path) as stream:
                    captured["auth"] = json.load(stream)
            else:
                captured["auth"] = None
            return mock.Mock(
                returncode=0,
                stdout='{"type":"text","part":{"text":"{\\"verdict\\":\\"success\\"}"}}\n',
                stderr="",
            )

        with mock.patch.object(judge, "OPENCODE", "/fake/opencode"), \
             mock.patch.object(judge, "OAI_KEY", "real-openai-secret"), \
             mock.patch.object(judge, "_judge_proxy", create=True, side_effect=fake_judge_proxy) as proxy, \
             mock.patch.object(judge.subprocess, "run", side_effect=run_opencode), \
             mock.patch.dict(os.environ, {
                 "OPENAI_API_KEY": "real-openai-secret",
                 "AI_GATEWAY_API_KEY": "real-vercel-secret",
             }, clear=False):
            text = judge.call_vision_via_opencode(
                "prompt", "unused.png", judge.JUDGE_MODEL, judge.JUDGE_VARIANT
            )

        self.assertEqual(text, '{"verdict":"success"}')
        proxy.assert_called_once_with("real-openai-secret")
        self.assertEqual(captured["cwd"], os.path.join(judge.ROOT, "configs", "judge"))
        self.assertEqual(captured["provider_env"], {
            "OPENAI_API_KEY": "bench-proxy-placeholder",
            "AI_GATEWAY_API_KEY": None,
        })
        self.assertEqual(captured["auth"]["openai"]["key"], "bench-proxy-placeholder")
        base_url = captured["config"]["provider"]["openai"]["options"]["baseURL"]
        self.assertEqual(base_url, "http://127.0.0.1:48765/v1")
        self.assertNotIn(":8790", json.dumps(captured["config"]))

    def test_nonzero_opencode_judge_response_fails(self):
        with mock.patch.object(judge, "OPENCODE", "/fake/opencode"), \
             mock.patch.object(judge, "OAI_KEY", "real-openai-secret"), \
             mock.patch.object(judge, "_judge_proxy", create=True, side_effect=fake_judge_proxy), \
             mock.patch.object(judge.subprocess, "run", return_value=mock.Mock(
                 returncode=9,
                 stdout='{"type":"text","part":{"text":"{\\"verdict\\":\\"success\\"}"}}',
                 stderr="route failed",
             )):
            with self.assertRaisesRegex(RuntimeError, "OpenCode judge failed"):
                judge.call_vision_via_opencode(
                    "prompt", "unused.png", judge.JUDGE_MODEL, judge.JUDGE_VARIANT
                )

    def test_empty_opencode_judge_response_fails(self):
        with mock.patch.object(judge, "OPENCODE", "/fake/opencode"), \
             mock.patch.object(judge, "OAI_KEY", "real-openai-secret"), \
             mock.patch.object(judge, "_judge_proxy", create=True, side_effect=fake_judge_proxy), \
             mock.patch.object(judge.subprocess, "run", return_value=mock.Mock(
                 returncode=0,
                 stdout='{"type":"step_finish","part":{}}',
                 stderr="",
             )):
            with self.assertRaisesRegex(RuntimeError, "empty response"):
                judge.call_vision_via_opencode(
                    "prompt", "unused.png", judge.JUDGE_MODEL, judge.JUDGE_VARIANT
                )


class DeterministicPostconditionTests(unittest.TestCase):
    def test_passing_atproto_postcondition_is_success_without_vision_call(self):
        task = {
            "id": "bsky-25",
            "app": "bluesky",
            "prompt": "Reply nice one",
            "solved_screen": "Published reply",
        }
        postcondition = {
            "schema": "bluesky-postcondition/v1",
            "source": "atproto-repo",
            "task": "bsky-25",
            "kind": "reply",
            "passed": True,
            "expected": {"exact_text": "nice one", "relation": "reply"},
            "observed": {"uri": "at://reply", "text": "nice one", "relation": "reply"},
        }
        with tempfile.TemporaryDirectory() as results:
            result_dir = os.path.join(results, "gpt_low__agent-device", "bsky-25")
            os.makedirs(result_dir)
            open(os.path.join(result_dir, "final.png"), "wb").close()
            with open(os.path.join(result_dir, "meta.json"), "w") as stream:
                json.dump({
                    "cell": "gpt_low:agent-device", "model": "gpt_low", "tool": "agent-device",
                    "task": "bsky-25", "tool_names": ["shell"], "n_tool_calls": 3,
                    "versions": {"harness": judge.harness_for("agent-device")},
                    "postcondition": postcondition,
                }, stream)
            with mock.patch.object(judge, "call_vision") as vision, \
                 mock.patch.object(judge.ledger, "mark"):
                score = judge.judge_one(result_dir, task, "gpt-5.6-luna", results_root=results)

        vision.assert_not_called()
        self.assertEqual(score["verdict"], "success")
        self.assertEqual(score["judge_model"], "deterministic:atproto-repo")
        self.assertEqual(score["judge_input"]["postcondition"], postcondition)


class JudgeFailClosedTests(unittest.TestCase):
    def test_existing_wrong_variant_score_is_rejudged_as_luna_xhigh(self):
        task = {"id": "task-1", "app": "element", "prompt": "Do it", "solved_screen": "Done"}
        with tempfile.TemporaryDirectory() as results:
            result_dir = os.path.join(results, "gpt_low__agent-device", "task-1")
            os.makedirs(result_dir)
            open(os.path.join(result_dir, "final.png"), "wb").close()
            with open(os.path.join(result_dir, "meta.json"), "w") as stream:
                json.dump({
                    "cell": "gpt_low:agent-device", "model": "gpt_low",
                    "tool": "agent-device", "task": "task-1", "versions": {
                        "harness": judge.harness_for("agent-device")
                    },
                }, stream)
            with open(os.path.join(result_dir, "score.json"), "w") as stream:
                json.dump({
                    "verdict": "success", "judge_model": judge.JUDGE_MODEL,
                    "judge_variant": "high", "task": "task-1",
                }, stream)
            with mock.patch.object(
                judge, "call_vision", return_value='{"verdict":"success","confidence":1,"reason":"ok"}'
            ) as vision, mock.patch.object(judge.ledger, "mark"), mock.patch.object(judge.time, "sleep"):
                score = judge.judge_one(result_dir, task, judge.JUDGE_MODEL, results_root=results)

        vision.assert_called_once()
        self.assertEqual(score["judge_model"], judge.JUDGE_MODEL)
        self.assertEqual(score["judge_variant"], judge.JUDGE_VARIANT)

    def test_selected_judging_exits_nonzero_for_unapproved_score(self):
        with tempfile.TemporaryDirectory() as results:
            os.makedirs(os.path.join(results, "gpt_low__agent-device", "task-1"))
            argv = ["judge.py", "--results", results, "--cell", "gpt_low__agent-device"]
            with mock.patch.object(sys, "argv", argv), \
                 mock.patch.object(judge, "OAI_KEY", "test-key"), \
                 mock.patch.object(judge, "OPENCODE", "/fake/opencode"), \
                 mock.patch.object(judge, "load_tasks", return_value={
                     "task-1": {"id": "task-1", "app": "element", "prompt": "Do it"}
                 }), \
                 mock.patch.object(judge, "judge_one", return_value={
                     "verdict": "success", "judge_model": judge.JUDGE_MODEL,
                     "judge_variant": "high", "task": "task-1",
                 }):
                with self.assertRaises(SystemExit) as raised:
                    judge.main()

        self.assertNotEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
