"""Platform contracts shared by execution-independent benchmark tooling."""
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class BenchmarkPlatform:
    id: str
    label: str
    device: str
    results_dir: str
    tools: tuple[str, ...]
    published_harnesses: Mapping[str, str]
    task_overrides: str | None = None

    def published_harness(self, tool):
        return self.published_harnesses[tool]

    @property
    def data_dir(self):
        return f"data/{self.id}/v1"

    @property
    def artifact_dir(self):
        return f"artifacts/{self.id}"

    @property
    def data_root(self):
        return "/" + self.data_dir

    @property
    def artifact_root(self):
        return "/" + self.artifact_dir

    @property
    def inventory_rel(self):
        return self.data_dir + "/inventory.json"


IOS = BenchmarkPlatform(
    id="ios",
    label="iOS",
    device="iOS Simulator",
    results_dir="data",
    tools=("argent", "agent-device", "none"),
    published_harnesses={
        "argent": "isolation-v3-progressive",
        "agent-device": "isolation-v4-default-set",
        "none": "isolation-v3-progressive",
    },
)
ANDROID = BenchmarkPlatform(
    id="android",
    label="Android",
    device="Android Emulator",
    results_dir="android-data",
    tools=("agent-device",),
    published_harnesses={"agent-device": "android-refresh-v2"},
    task_overrides="android-solved-screens.json",
)
PLATFORMS = {platform.id: platform for platform in (IOS, ANDROID)}
