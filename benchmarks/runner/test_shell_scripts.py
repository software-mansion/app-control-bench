#!/usr/bin/env python3
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest


BENCHMARKS = Path(__file__).resolve().parents[1]


class ShellScriptTests(unittest.TestCase):
    def write_executable(self, path, contents):
        path.write_text(contents)
        path.chmod(0o755)

    def test_run_all_passes_every_scope_to_each_doctor_call(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            fake_bin = temp_path / "bin"
            fake_bin.mkdir()
            calls = temp_path / "python-calls.tsv"
            self.write_executable(
                fake_bin / "python3",
                """#!/usr/bin/env bash
{
  printf '%s' "$1"
  shift
  printf '\\t%s' "$@"
  printf '\\n'
} >> "$BENCH_TEST_LOG"
""",
            )
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "BENCH_TEST_LOG": str(calls),
                "ONLY": "gpt",
                "TOOLS": "argent",
                "APPS": "bluesky",
                "SKIP_JUDGE": "1",
            }

            completed = subprocess.run(
                ["bash", str(BENCHMARKS / "run_all.sh")],
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            recorded = [line.split("\t") for line in calls.read_text().splitlines()]
            doctor_calls = [call for call in recorded if call[0] == "runner/doctor.py"]
            self.assertEqual(
                doctor_calls,
                [
                    [
                        "runner/doctor.py",
                        "--preflight",
                        "--models",
                        "gpt",
                        "--tools",
                        "argent",
                        "--apps",
                        "bluesky",
                    ],
                    [
                        "runner/doctor.py",
                        "--heal",
                        "--models",
                        "gpt",
                        "--tools",
                        "argent",
                        "--apps",
                        "bluesky",
                    ],
                    [
                        "runner/doctor.py",
                        "--models",
                        "gpt",
                        "--tools",
                        "argent",
                        "--apps",
                        "bluesky",
                    ],
                ],
            )

    def test_bluesky_backend_signal_cleanup_removes_only_owned_state_after_stop(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            fake_bin = temp_path / "bin"
            fake_bin.mkdir()
            dev_env = temp_path / "dev-env"
            (dev_env / "node_modules" / ".bin").mkdir(parents=True)
            (dev_env / "bench-server.ts").touch()
            events = temp_path / "events.log"
            state_file = temp_path / "state-dir"

            dispatcher = fake_bin / "fake-service"
            self.write_executable(
                dispatcher,
                """#!/usr/bin/env bash
set -eu
name="${0##*/}"
case "$name" in
  initdb)
    while [ "$#" -gt 0 ]; do
      if [ "$1" = "-D" ]; then
        pg_dir="$2"
        break
      fi
      shift
    done
    mkdir -p "$pg_dir"
    dirname "$pg_dir" > "$BENCH_TEST_STATE_FILE"
    printf 'postgres-initialized\\n' >> "$BENCH_TEST_EVENTS"
    ;;
  pg_isready)
    exit 1
    ;;
  pg_ctl)
    case " $* " in
      *" stop "*) printf 'postgres-stopped\\n' >> "$BENCH_TEST_EVENTS" ;;
      *" start "*) printf 'postgres-started\\n' >> "$BENCH_TEST_EVENTS" ;;
    esac
    ;;
  redis-cli)
    case " $* " in
      *" ping "*) exit 1 ;;
      *" shutdown nosave "*) printf 'redis-stopped\\n' >> "$BENCH_TEST_EVENTS" ;;
    esac
    ;;
  redis-server)
    printf 'redis-started\\n' >> "$BENCH_TEST_EVENTS"
    ;;
esac
""",
            )
            for command in ("initdb", "pg_ctl", "pg_isready", "redis-server", "redis-cli"):
                (fake_bin / command).symlink_to(dispatcher)

            self.write_executable(
                dev_env / "node_modules" / ".bin" / "ts-node",
                """#!/usr/bin/env bash
trap 'printf "server-stopped\\n" >> "$BENCH_TEST_EVENTS"; exit 0' TERM INT
printf 'server-started\\n' >> "$BENCH_TEST_EVENTS"
while :; do sleep 0.1; done
""",
            )

            foreign_state = Path(
                tempfile.mkdtemp(prefix="app-control-bench-bluesky.foreign-", dir="/tmp")
            )
            self.addCleanup(shutil.rmtree, foreign_state, True)
            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "BENCH_BSKY_DEV_ENV": str(dev_env),
                "BENCH_TEST_EVENTS": str(events),
                "BENCH_TEST_STATE_FILE": str(state_file),
            }
            process = subprocess.Popen(
                ["bash", str(BENCHMARKS / "start_bluesky_backend.sh")],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            owned_state = None
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if events.exists() and "server-started" in events.read_text():
                        break
                    if process.poll() is not None:
                        stdout, stderr = process.communicate()
                        self.fail(f"backend exited before startup: {stdout}\n{stderr}")
                    time.sleep(0.02)
                else:
                    self.fail("backend did not reach the server startup")

                owned_state = Path(state_file.read_text().strip())
                self.assertTrue(owned_state.is_dir())
                process.terminate()
                stdout, stderr = process.communicate(timeout=5)
                self.assertIn(
                    process.returncode,
                    (-signal.SIGTERM, 128 + signal.SIGTERM),
                    (stdout, stderr),
                )

                recorded = events.read_text().splitlines()
                self.assertLess(recorded.index("server-stopped"), recorded.index("redis-stopped"))
                self.assertLess(recorded.index("redis-stopped"), recorded.index("postgres-stopped"))
                self.assertFalse(owned_state.exists())
                self.assertTrue(foreign_state.is_dir())
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                if owned_state is not None:
                    shutil.rmtree(owned_state, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
