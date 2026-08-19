import os
import sys
import json
import tempfile
import unittest


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import report_data


class PlatformExportTests(unittest.TestCase):
    def test_android_is_an_isolated_agent_device_only_namespace(self):
        platform = report_data.ANDROID

        self.assertEqual(platform.tools, ("agent-device",))
        self.assertEqual(platform.data_root, "/data/android/v1")
        self.assertEqual(platform.artifact_root, "/artifacts/android")
        self.assertEqual(platform.published_harness("agent-device"), "android-refresh-v2")

    def test_manifest_and_provenance_identify_android_without_ios_element_version(self):
        tasks = [
            {"id": "bsky-01", "app": "bluesky"},
            {"id": "element-01", "app": "element"},
        ]
        dataset = report_data.Dataset(
            tasks=tasks,
            tmap={task["id"]: task for task in tasks},
            models=[], rows={}, annulled=frozenset(), platform=report_data.ANDROID,
        )

        meta = report_data.build_report_meta(dataset, "now", "build")

        self.assertEqual(meta["manifest"]["platform"], {"id": "android", "label": "Android"})
        self.assertTrue(meta["provenance"]["judgeLine"].endswith("Android Emulator"))
        self.assertIn("not pass@1", meta["provenance"]["resultPolicy"])
        self.assertEqual(meta["provenance"]["releaseToolVersions"]["agent-device"], "0.20.10")
        self.assertIn("bluesky", meta["provenance"]["appVersions"])
        self.assertNotIn("element", meta["provenance"]["appVersions"])

    def test_android_annuls_exactly_the_five_unsupported_element_tasks(self):
        tasks, _ = report_data.load_tasks(report_data.ANDROID)

        self.assertEqual(
            {task["id"] for task in tasks if task.get("annulled")},
            {"element-11", "element-17", "element-18", "element-20", "element-22"},
        )


class PublishedCostTests(unittest.TestCase):
    def test_parse_chat_preserves_provider_normalized_token_buckets(self):
        event = {
            "type": "step_finish",
            "part": {
                "cost": 0,
                "tokens": {
                    "total": 180, "input": 100, "output": 20, "reasoning": 5,
                    "cache": {"read": 50, "write": 5},
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "transcript.jsonl")
            with open(path, "w") as stream:
                stream.write(json.dumps(event) + "\n")
            _, cost, usage = report_data.parse_chat(path)

        self.assertEqual(cost, 0)
        self.assertEqual(
            usage,
            {"input": 100, "cached_input": 50, "cache_write": 5,
             "output": 20, "reasoning": 5},
        )

    def test_calculates_subscription_cost_from_each_token_bucket(self):
        cost, basis = report_data.published_cost(
            {"model": "gpt_low"}, 0.0,
            {"input": 1_000_000, "cached_input": 1_000_000,
             "cache_write": 1_000_000, "output": 1_000_000, "reasoning": 1_000_000},
        )

        self.assertEqual(cost, 10.575)
        self.assertEqual(basis, "token-calculated")


class HistoricalToolVersionTests(unittest.TestCase):
    def test_export_uses_captured_version_instead_of_next_run_pin(self):
        dataset = report_data.Dataset(
            tasks=[{"id": "element-01", "app": "element"}],
            tmap={"element-01": {"id": "element-01", "app": "element"}},
            models=["gpt_low"],
            rows={
                ("gpt_low", "agent-device", "element-01"): {
                    "meta": {"versions": {"tool": "0.17.6"}}
                }
            },
            annulled=frozenset(),
        )

        versions = report_data.observed_tool_versions(dataset)

        self.assertEqual(versions["agent-device"], "0.17.6")
        self.assertEqual(
            report_data.tool_entry("agent-device", versions)["version"], "0.17.6"
        )
        self.assertNotEqual(versions["agent-device"], report_data.TOOL_VERSIONS["agent-device"])

    def test_existing_versionless_rows_are_reported_as_unknown(self):
        dataset = report_data.Dataset(
            tasks=[{"id": "element-01", "app": "element"}],
            tmap={"element-01": {"id": "element-01", "app": "element"}},
            models=["gpt_low"],
            rows={("gpt_low", "agent-device", "element-01"): {"meta": {}}},
            annulled=frozenset(),
        )

        versions = report_data.observed_tool_versions(dataset)

        self.assertIsNone(versions["agent-device"])

    def test_tools_without_rows_use_the_next_run_pin(self):
        dataset = report_data.Dataset(
            tasks=[{"id": "element-01", "app": "element"}],
            tmap={"element-01": {"id": "element-01", "app": "element"}},
            models=["gpt_low"],
            rows={},
            annulled=frozenset(),
        )

        versions = report_data.observed_tool_versions(dataset)

        self.assertEqual(versions["agent-device"], "0.20.10")


if __name__ == "__main__":
    unittest.main()
