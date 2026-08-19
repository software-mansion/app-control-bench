import json
import os
import subprocess
import sys
import unittest
from unittest import mock


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bluesky_control
import sim_device


class BackendIdentityTests(unittest.TestCase):
    def test_backend_identity_mismatch_fails_closed(self):
        current = {
            "pds_url": "http://localhost:3000",
            "appview_did": "did:web:new",
            "bench_did": "did:plc:new",
        }
        with mock.patch.object(
            sim_device,
            "load_state",
            return_value={"bluesky_backend_identity": {**current, "bench_did": "did:plc:old"}},
        ):
            with self.assertRaisesRegex(sim_device.DeviceError, "backend identity changed"):
                sim_device.require_bluesky_backend_identity(current)

    def test_backend_identity_can_be_initialized_only_after_authenticated_clone(self):
        identity = {
            "pds_url": "http://localhost:3000",
            "appview_did": "did:web:appview",
            "bench_did": "did:plc:bench",
        }
        with mock.patch.object(sim_device, "save_state") as save_state:
            with self.assertRaisesRegex(sim_device.DeviceError, "authenticated clone"):
                sim_device.confirm_bluesky_clone_session(identity, clone_authenticated=False, refreshed=True)
            save_state.assert_not_called()

            sim_device.confirm_bluesky_clone_session(identity, clone_authenticated=True, refreshed=True)

        update = save_state.call_args.kwargs
        self.assertEqual(update["bluesky_backend_identity"], identity)
        self.assertIn("clone_session_verified_ts", update)
        self.assertIn("session_refreshed_ts", update)

    def test_refresh_does_not_stamp_success_before_clone_verification(self):
        golden = {"name": "golden", "udid": "G"}
        launched = subprocess.CompletedProcess([], 0, "", "")
        shutdown = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(sim_device, "load_state", return_value={}), \
             mock.patch.object(sim_device, "check_golden", return_value=golden), \
             mock.patch.object(sim_device, "boot"), \
             mock.patch.object(sim_device, "_simctl", side_effect=[launched, mock.DEFAULT, shutdown]), \
             mock.patch.object(sim_device.time, "sleep"), \
             mock.patch.object(sim_device, "golden_info", return_value={**golden, "state": "Shutdown"}), \
             mock.patch.object(sim_device, "save_state") as save_state:
            self.assertTrue(sim_device.refresh_golden_sessions(force=True))
        save_state.assert_not_called()


class CloneSessionValidationTests(unittest.TestCase):
    def test_each_validation_owns_and_stops_its_daemon_even_when_open_fails(self):
        calls = []
        open_count = 0

        def run(command, **kwargs):
            nonlocal open_count
            calls.append((command, kwargs))
            if command[1] == "open":
                open_count += 1
                if open_count == 2:
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            if command[1] == "snapshot":
                tree = {
                    "role": "application",
                    "children": [
                        {"role": "tab", "label": "Following", "identifier": "followingFeedPage"},
                        {"role": "staticText", "label": "whiskers.test"},
                    ],
                }
                return subprocess.CompletedProcess(
                    command, 0, json.dumps({"success": True, "data": {"tree": tree}}), ""
                )
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(bluesky_control.subprocess, "run", side_effect=run):
            bluesky_control.validate_clone_session("CLONE", "/tmp/shared-benchmark-state")
            with self.assertRaises(subprocess.TimeoutExpired):
                bluesky_control.validate_clone_session("CLONE", "/tmp/shared-benchmark-state")

        opens = [call for call in calls if call[0][1] == "open"]
        stops = [call for call in calls if call[0][1:3] == ["daemon", "stop"]]
        open_state_dirs = [call[1]["env"]["AGENT_DEVICE_STATE_DIR"] for call in opens]
        stop_state_dirs = [call[1]["env"]["AGENT_DEVICE_STATE_DIR"] for call in stops]

        self.assertEqual(len(opens), 2)
        self.assertEqual(len(set(open_state_dirs)), 2)
        self.assertNotIn("/tmp/shared-benchmark-state", open_state_dirs)
        self.assertCountEqual(stop_state_dirs, open_state_dirs)
        self.assertCountEqual([call[0][-1] for call in stops], open_state_dirs)
        self.assertTrue(all(not os.path.exists(state_dir) for state_dir in open_state_dirs))

    def test_accepts_authenticated_following_feed_from_actual_clone(self):
        opened = subprocess.CompletedProcess([], 0, json.dumps({"success": True}), "")
        snapshot = subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "success": True,
                    "data": {
                        "tree": {
                            "role": "application",
                            "children": [
                                {"role": "tab", "label": "Following", "identifier": "followingFeedPage"},
                                {"role": "staticText", "label": "whiskers.test"},
                            ],
                        }
                    },
                }
            ),
            "",
        )
        closed = subprocess.CompletedProcess([], 0, "", "")
        stopped = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(
            bluesky_control.subprocess, "run", side_effect=[opened, snapshot, closed, stopped]
        ) as run:
            evidence = bluesky_control.validate_clone_session("CLONE", "/tmp/state")

        self.assertTrue(evidence["authenticated"])
        self.assertEqual(evidence["clone_udid"], "CLONE")
        targeted_commands = [
            call.args[0] for call in run.call_args_list if call.args[0][1] in ("open", "snapshot", "close")
        ]
        self.assertTrue(all("CLONE" in command for command in targeted_commands))

    def test_rejects_logged_out_clone(self):
        opened = subprocess.CompletedProcess([], 0, "", "")
        snapshot = subprocess.CompletedProcess(
            [], 0, json.dumps({"success": True, "data": {"tree": {"label": "Sign in"}}}), ""
        )
        closed = subprocess.CompletedProcess([], 0, "", "")
        stopped = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(
            bluesky_control.subprocess, "run", side_effect=[opened, snapshot, closed, stopped]
        ):
            with self.assertRaisesRegex(bluesky_control.BlueskyControlError, "not authenticated"):
                bluesky_control.validate_clone_session("CLONE", "/tmp/state")


class MutationPostconditionTests(unittest.TestCase):
    TARGET = "at://did:plc:whiskers/app.bsky.feed.post/first"

    def post(self, uri, text, **value):
        return {"uri": uri, "value": {"$type": "app.bsky.feed.post", "text": text, **value}}

    def test_exact_reply_requires_target_parent_relation(self):
        result = bluesky_control.evaluate_mutation_postcondition(
            "bsky-25",
            [self.post(
                "at://did:plc:bench/app.bsky.feed.post/reply",
                "nice one",
                reply={"root": {"uri": self.TARGET}, "parent": {"uri": self.TARGET}},
            )],
            self.TARGET,
        )

        self.assertEqual(result["schema"], "bluesky-postcondition/v1")
        self.assertEqual(result["source"], "atproto-repo")
        self.assertEqual(result["task"], "bsky-25")
        self.assertEqual(result["kind"], "reply")
        self.assertTrue(result["passed"])
        self.assertEqual(result["expected"]["exact_text"], "nice one")
        self.assertEqual(result["observed"]["uri"], "at://did:plc:bench/app.bsky.feed.post/reply")

        wrong_parent = bluesky_control.evaluate_mutation_postcondition(
            "bsky-25",
            [self.post(
                "at://did:plc:bench/app.bsky.feed.post/reply",
                "nice one",
                reply={"root": {"uri": "other"}, "parent": {"uri": "other"}},
            )],
            self.TARGET,
        )
        self.assertFalse(wrong_parent["passed"])

    def test_exact_standalone_posts_reject_reply_or_quote_records(self):
        standalone = self.post(
            "at://did:plc:bench/app.bsky.feed.post/post",
            "hello from the benchmark",
        )
        result = bluesky_control.evaluate_mutation_postcondition("bsky-28", [standalone], self.TARGET)
        self.assertTrue(result["passed"])
        self.assertEqual(result["kind"], "post")

        quoted = self.post(
            "at://did:plc:bench/app.bsky.feed.post/quote",
            "hello from the benchmark",
            embed={"$type": "app.bsky.embed.record", "record": {"uri": self.TARGET}},
        )
        self.assertFalse(
            bluesky_control.evaluate_mutation_postcondition("bsky-28", [quoted], self.TARGET)["passed"]
        )

    def test_exact_quote_requires_embedded_target(self):
        quoted = self.post(
            "at://did:plc:bench/app.bsky.feed.post/quote",
            "sharing this",
            embed={"$type": "app.bsky.embed.record", "record": {"uri": self.TARGET}},
        )
        result = bluesky_control.evaluate_mutation_postcondition("bsky-30", [quoted], self.TARGET)

        self.assertTrue(result["passed"])
        self.assertEqual(result["kind"], "quote")
        self.assertEqual(result["expected"]["target_uri"], self.TARGET)

    def test_non_mutation_task_has_no_postcondition(self):
        self.assertIsNone(bluesky_control.assert_mutation_postcondition("bsky-03"))


if __name__ == "__main__":
    unittest.main()
