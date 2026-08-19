import os
import sys
import json
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import judge


class AndroidTaskCatalogTests(unittest.TestCase):
    def test_full_catalog_uses_android_solved_screen_overrides(self):
        tasks = judge.load_tasks("android")

        self.assertIn("New room", tasks["element-06"]["solved_screen"])
        self.assertNotIn("Create a space", tasks["element-06"]["solved_screen"])

    def test_every_full_element_task_has_an_android_description(self):
        with open(os.path.join(judge.ROOT, "tasks", judge.ANDROID.task_overrides)) as stream:
            catalog = json.load(stream)
        overrides = catalog["solved_screens"]

        tasks = {
            task_id: task
            for task_id, task in judge.load_tasks("android").items()
            if task_id.startswith("element-")
        }

        self.assertEqual(set(tasks), {task_id for task_id in overrides if task_id.startswith("element-")})
        evidence = catalog["evidence"]
        classified = (
            set(evidence["observed"])
            | set(evidence["context_required"])
            | set(evidence["unsupported"])
        )
        self.assertEqual(set(tasks), classified)
        self.assertEqual(
            len(classified),
            sum(
                len(evidence[key])
                for key in ("observed", "context_required", "unsupported")
            ),
        )
        pattern = evidence["primary_capture_pattern"]
        for task_id in tasks:
            self.assertTrue(os.path.exists(os.path.join(os.path.dirname(judge.ROOT), pattern.format(task_id=task_id))))
        for path in evidence["secondary_captures"].values():
            self.assertTrue(os.path.exists(os.path.join(os.path.dirname(judge.ROOT), path)))


class JudgeConsistencyTests(unittest.TestCase):
    def test_passing_atproto_postcondition_is_success_without_model_process(self):
        task = judge.load_tasks("android")["bsky-28"]
        with tempfile.TemporaryDirectory() as results:
            result_dir = os.path.join(results, "gpt_low__agent-device", "bsky-28")
            os.makedirs(result_dir)
            open(os.path.join(result_dir, "final.png"), "wb").close()
            postcondition = {
                "schema": "bluesky-postcondition/v1", "source": "atproto-repo",
                "task": "bsky-28", "kind": "post", "passed": True,
            }
            with open(os.path.join(result_dir, "meta.json"), "w") as stream:
                json.dump({
                    "returncode": 0, "cell": "gpt_low:agent-device", "model": "gpt_low",
                    "tool": "agent-device", "task": "bsky-28", "tool_names": ["shell"],
                    "n_tool_calls": 2, "postcondition": postcondition,
                    "versions": {"harness": "android-refresh-v2"},
                }, stream)
            with mock.patch.object(judge, "call_vision") as model_process, \
                 mock.patch.object(judge.ledger, "mark"), mock.patch.object(judge.time, "sleep"):
                score = judge.judge_one(
                    result_dir, task, "openai/gpt-5.6-luna", "xhigh", False,
                    results, expected_harness="android-refresh-v2",
                    device_context="an Android Emulator",
                )
            model_process.assert_not_called()
            self.assertEqual(score["verdict"], "success")
            with open(os.path.join(result_dir, "score.json")) as stream:
                written = json.load(stream)
        self.assertEqual(written["judge_model"], "deterministic:atproto-repo")
        self.assertEqual(written["judge_input"]["postcondition"], postcondition)

    def test_reports_divergent_verdicts_for_identical_task_screenshots(self):
        with tempfile.TemporaryDirectory() as results:
            for model, verdict in (("gpt_low", "success"), ("haiku_low", "fail")):
                result_dir = os.path.join(results, f"{model}__agent-device", "element-06")
                os.makedirs(result_dir)
                with open(os.path.join(result_dir, "final.png"), "wb") as stream:
                    stream.write(b"same screenshot")
                with open(os.path.join(result_dir, "score.json"), "w") as stream:
                    json.dump(
                        {
                            "verdict": verdict,
                            "judge_input": {"prompt": "Open create-room", "n_tool_calls": 4},
                        },
                        stream,
                    )

            conflicts = judge.find_identical_screenshot_conflicts(
                results,
                ["gpt_low__agent-device", "haiku_low__agent-device"],
                ["element-06"],
            )

        self.assertEqual(
            conflicts,
            [
                {
                    "task": "element-06",
                    "cells": ["gpt_low__agent-device", "haiku_low__agent-device"],
                    "verdicts": ["success", "fail"],
                    "same_judge_input": True,
                }
            ],
        )

    def test_reports_but_does_not_equate_different_tool_activity(self):
        with tempfile.TemporaryDirectory() as results:
            for model, verdict, calls in (
                ("gpt_low", "success", 4),
                ("haiku_low", "fail", 9),
            ):
                result_dir = os.path.join(results, f"{model}__agent-device", "element-12")
                os.makedirs(result_dir)
                with open(os.path.join(result_dir, "final.png"), "wb") as stream:
                    stream.write(b"same screenshot")
                with open(os.path.join(result_dir, "score.json"), "w") as stream:
                    json.dump(
                        {
                            "verdict": verdict,
                            "judge_input": {"prompt": "Book Club notifications", "n_tool_calls": calls},
                        },
                        stream,
                    )

            conflicts = judge.find_identical_screenshot_conflicts(
                results,
                ["gpt_low__agent-device", "haiku_low__agent-device"],
                ["element-12"],
            )

        self.assertFalse(conflicts[0]["same_judge_input"])


if __name__ == "__main__":
    unittest.main()
