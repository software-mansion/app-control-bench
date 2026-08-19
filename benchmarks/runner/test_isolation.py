#!/usr/bin/env python3
"""Local unit tests for isolation.py — no devices, no network, no real bench processes.
Deliberately SAFE on a shared machine: teardown is tested against dummy `sleep` processes with
bespoke cmdline markers (KILL_PATTERNS is monkeypatched), never against the real patterns.

    python3 runner/test_isolation.py
"""
import json
import http.server
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import isolation

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok   {name}")


def test_lock():
    lock = os.path.join(tempfile.mkdtemp(prefix="acb-test-"), "lock")
    # holder: a child that acquires and sleeps
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r});"
         f"import os; os.environ['BENCH_LOCK']={lock!r};"
         "import isolation; isolation.LOCK_PATH=os.environ['BENCH_LOCK'];"
         "isolation.acquire_lock(label='holder'); print('HELD', flush=True);"
         "import time; time.sleep(30)"],
        stdout=subprocess.PIPE, text=True)
    line = holder.stdout.readline()
    check("lock: holder acquired", line.strip() == "HELD", line)
    # contender: must exit nonzero with the loud message
    r = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r});"
         "import isolation; isolation.LOCK_PATH=" + repr(lock) + ";"
         "isolation.acquire_lock(label='contender')"],
        capture_output=True, text=True)
    check("lock: second stream refused", r.returncode != 0)
    check("lock: refusal names the holder", "holder" in r.stderr and "one benchmark stream" in r.stderr,
          r.stderr[-200:])
    # lock_holder() sees it
    old = isolation.LOCK_PATH
    isolation.LOCK_PATH = lock
    h = isolation.lock_holder()
    check("lock: lock_holder reports holder", h is not None and "holder" in h, str(h))
    holder.kill()
    holder.wait()
    time.sleep(0.2)
    check("lock: kernel releases on death", isolation.lock_holder() is None)
    isolation.LOCK_PATH = old


def test_teardown_dummies():
    marker = f"acb-dummy-{os.getpid()}"
    # isolate the registry so the test never touches a real bench's pid file
    old_pids_path = isolation.PIDS_PATH
    isolation.PIDS_PATH = os.path.join(tempfile.mkdtemp(prefix="acb-test-"), "pids.json")
    # marker lives INSIDE the -c string (a bare `sleep` would get exec-optimized and lose the
    # cmdline). Each owned dummy also forks a CHILD sleep whose cmdline does NOT match — the
    # tree-kill must take it down with its parent. Owned = registered at spawn, like the harness.
    procs = [subprocess.Popen(["bash", "-c", f"true {marker}-{i}; sleep 300 & wait"],
                              start_new_session=True) for i in range(2)]
    for p in procs:
        isolation.register_pid(p.pid, "test-dummy")
    # NOT registered; DEVNULL stdout so its orphaned child can't pin our pipe after foreign.kill(),
    # and a short sleep so that orphan doesn't linger long after the test
    foreign = subprocess.Popen(["bash", "-c", f"true {marker}-foreign; sleep 45"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(0.5)
    check("teardown: dummies visible to pgrep", len(isolation._pgrep(marker)) == 3)
    kids = [k for p in procs for k in isolation._descendants([p.pid])]
    check("teardown: owned dummies have children", len(kids) == 2, str(kids))
    old_pat, old_scope = isolation.KILL_PATTERNS, isolation.KILL_SCOPE
    isolation.KILL_PATTERNS, isolation.KILL_SCOPE = [marker], "owned"
    try:
        rep = isolation.teardown_all("pre")
    finally:
        isolation.KILL_PATTERNS, isolation.KILL_SCOPE, isolation.PIDS_PATH = \
            old_pat, old_scope, old_pids_path
    killed = set(rep["killed"][0]["pids"])
    check("teardown: report lists owned pids + their children",
          killed == {p.pid for p in procs} | set(kids), str(rep))
    check("teardown: children are dead too", isolation._alive(kids) == [])
    check("teardown: stray (unregistered) match survives + is reported",
          isolation._alive([foreign.pid]) == [foreign.pid]
          and rep["killed"][0]["strays"] == [foreign.pid], str(rep))
    foreign.kill()
    foreign.wait()
    for p in procs:
        p.wait()
    check("teardown: nothing owned remains", isolation._pgrep(marker) == [])


def test_run_opencode_timeout():
    # a stubborn tree: parent bash spawns a child sleep; killpg must take both
    t0 = time.time()
    with tempfile.TemporaryFile(mode="w+") as tf:
        rc, err, timed_out = isolation.run_opencode(
            ["bash", "-c", "sleep 120 & sleep 120"], cwd="/tmp", timeout=2, stdout_file=tf)
    check("run_opencode: timeout flagged", timed_out and rc == -1 and err == "TIMEOUT", f"{rc} {err}")
    check("run_opencode: returned promptly", time.time() - t0 < 20, f"{time.time() - t0:.1f}s")


def test_run_opencode_stdout_is_pipe_backed():
    # OpenCode's Bun runtime fstats stdout during startup. The benchmark denies the sandboxed agent
    # read access to the result tree, so handing the child the transcript file descriptor makes
    # startup fail with EPERM before the first model turn. The child must see a pipe while the parent
    # remains responsible for recording its transcript.
    with tempfile.TemporaryFile(mode="w+") as tf:
        rc, err, timed_out = isolation.run_opencode(
            [sys.executable, "-c",
             "import os, stat, sys; sys.exit(2) if not stat.S_ISFIFO(os.fstat(1).st_mode) "
             "else print('transcript-event')"],
            cwd="/tmp", timeout=10, stdout_file=tf)
        tf.seek(0)
        recorded = tf.read()
    check("run_opencode: child stdout is pipe-backed", rc == 0 and not timed_out, f"{rc} {err}")
    check("run_opencode: parent records stdout", recorded == "transcript-event\n", repr(recorded))


def test_run_opencode_stages_placeholder_credentials_only():
    secrets = {
        "AI_GATEWAY_API_KEY": "real-vercel-secret",
        "OPENAI_API_KEY": "real-openai-secret",
        "GITHUB_TOKEN": "real-unrelated-secret",
        # The current Vercel caller also aliases its real Gateway key through this variable.
        "ANTHROPIC_API_KEY": "real-vercel-secret",
    }
    with tempfile.TemporaryDirectory(prefix="acb-test-opencode-env-") as run_dir, \
         tempfile.TemporaryFile(mode="w+") as transcript:
        old_pids_path = isolation.PIDS_PATH
        isolation.PIDS_PATH = os.path.join(run_dir, "pids.json")
        try:
            with mock.patch.dict(os.environ, secrets, clear=False), \
                 mock.patch.object(isolation, "register_pid"):
                rc, err, timed_out = isolation.run_opencode(
                    [
                        sys.executable,
                        "-c",
                        "import json, os; "
                        "xdg=os.environ.get('XDG_DATA_HOME'); "
                        "auth_path=os.path.join(xdg, 'opencode', 'auth.json') if xdg else None; "
                        "print(json.dumps({'env': {k: os.environ.get(k) for k in "
                        "['AI_GATEWAY_API_KEY', 'OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GITHUB_TOKEN']}, "
                        "'auth': json.load(open(auth_path)) if auth_path and os.path.exists(auth_path) "
                        "else None, 'auth_path': auth_path}))",
                    ],
                    cwd=run_dir,
                    timeout=10,
                    stdout_file=transcript,
                    env=secrets,
                )
        finally:
            isolation.PIDS_PATH = old_pids_path
        transcript.seek(0)
        observed = json.loads(transcript.read())

    serialized = json.dumps(observed, sort_keys=True)
    check("run_opencode: credential-isolated child succeeds", rc == 0 and not timed_out, f"{rc} {err}")
    check(
        "run_opencode: real provider secrets never reach child env or auth",
        "real-vercel-secret" not in serialized and "real-openai-secret" not in serialized,
        serialized,
    )
    check(
        "run_opencode: OpenCode receives provider placeholders",
        observed["env"] == {
            "AI_GATEWAY_API_KEY": None,
            "OPENAI_API_KEY": "bench-proxy-placeholder",
            "ANTHROPIC_API_KEY": "bench-proxy-placeholder",
            "GITHUB_TOKEN": None,
        },
        serialized,
    )
    check(
        "run_opencode: staged auth contains placeholders only",
        observed["auth"] == {
            "openai": {"type": "api", "key": "bench-proxy-placeholder"},
            "anthropic": {"type": "api", "key": "bench-proxy-placeholder"},
        }
        and os.path.commonpath([run_dir, observed["auth_path"]]) == run_dir,
        serialized,
    )


def test_openai_proxy_owns_upstream_credential():
    captured = {}

    class Upstream(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("content-length", "0")))
            captured.update(
                headers={key.lower(): value for key, value in self.headers.items()},
                body=json.loads(body),
            )
            response = b'{"ok":true}'
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_args):
            pass

    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        proxy_port = reservation.getsockname()[1]
    proxy = subprocess.Popen(
        ["node", os.path.join(os.path.dirname(__file__), "anthropic-thinking-proxy.js"), str(proxy_port)],
        env={
            "PATH": os.environ.get("PATH", ""),
            "BENCH_PROXY_PROVIDER": "openai",
            "BENCH_PROXY_UPSTREAM": f"http://127.0.0.1:{upstream.server_port}",
            "BENCH_EFFORT_JSON": '{"gpt54":"low"}',
            "OPENAI_API_KEY": "real-openai-secret",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 5
        while time.time() < deadline and not isolation._port_up(proxy_port):
            time.sleep(0.05)
        request = urllib.request.Request(
            f"http://127.0.0.1:{proxy_port}/v1/chat/completions",
            data=json.dumps({"model": "gpt-5.4-mini", "messages": []}).encode(),
            headers={
                "Authorization": "Bearer bench-proxy-placeholder",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                response.read()
        except (urllib.error.URLError, TimeoutError):
            pass
    finally:
        proxy.terminate()
        proxy.wait(timeout=5)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=5)

    check(
        "proxy: OpenAI upstream receives proxy-owned credential",
        captured.get("headers", {}).get("authorization") == "Bearer real-openai-secret",
        json.dumps(captured, sort_keys=True),
    )
    check(
        "proxy: OpenAI request keeps configured effort",
        captured.get("body", {}).get("reasoning_effort") == "low",
        json.dumps(captured, sort_keys=True),
    )


def test_start_proxies_scopes_credentials_to_provider_proxy():
    fake_process = mock.Mock(pid=1234)
    ambient = {
        "OPENAI_API_KEY": "real-openai-secret",
        "AI_GATEWAY_API_KEY": "unrelated-vercel-secret",
        "BENCH_PROXY_UPSTREAM": "http://attacker.invalid",
    }
    with tempfile.TemporaryDirectory(prefix="acb-test-proxy-env-") as logdir, \
         mock.patch.dict(os.environ, ambient, clear=False), \
         mock.patch.object(isolation, "_port_up", side_effect=[False, True, True]), \
         mock.patch.object(isolation, "register_pid"), \
         mock.patch.object(isolation.subprocess, "Popen", return_value=fake_process) as popen:
        isolation.start_proxies(
            {"gpt54": "low"},
            logdir,
            providers=["openai"],
        )

    command = popen.call_args.args[0]
    proxy_env = popen.call_args.kwargs["env"]
    check(
        "start_proxies: OpenAI uses credential-owning proxy",
        command[1].endswith("anthropic-thinking-proxy.js")
        and proxy_env.get("BENCH_PROXY_PROVIDER") == "openai",
        f"command={command} provider={proxy_env.get('BENCH_PROXY_PROVIDER')}",
    )
    check(
        "start_proxies: each proxy receives only its own upstream credential",
        proxy_env.get("OPENAI_API_KEY") == "real-openai-secret"
        and "AI_GATEWAY_API_KEY" not in proxy_env
        and "BENCH_PROXY_UPSTREAM" not in proxy_env,
        f"keys={sorted(proxy_env)}",
    )


def test_reset_hook():
    outdir = tempfile.mkdtemp(prefix="acb-test-hook-")
    rep = isolation.run_reset_hook(["bash", "-c", "echo seeded"], outdir, timeout=10, label="ok-hook")
    check("reset_hook: success rc=0", rep["rc"] == 0 and rep["duration_s"] >= 0, str(rep))
    check("reset_hook: log captured", "seeded" in open(os.path.join(outdir, "reset-server.log")).read())
    try:
        isolation.run_reset_hook(["bash", "-c", "exit 3"], outdir, timeout=10, label="bad-hook")
        check("reset_hook: hard-fail raises", False)
    except isolation.ResetError:
        check("reset_hook: hard-fail raises", True)
    os.environ["BENCH_RESET_SOFT"] = "1"
    try:
        rep = isolation.run_reset_hook(["bash", "-c", "exit 3"], outdir, timeout=10, label="soft-hook")
        check("reset_hook: soft mode continues", rep["rc"] == 3, str(rep))
    finally:
        del os.environ["BENCH_RESET_SOFT"]


def test_env_manifest():
    outdir = tempfile.mkdtemp(prefix="acb-test-env-")
    p = isolation.write_env_manifest(outdir, ts_start=1, effort={"gpt54": "high"}, clone_udid="X")
    d = json.load(open(p))
    check("env.json: round-trips", d["schema"] == 1 and d["effort"] == {"gpt54": "high"} and d["clone_udid"] == "X")


def test_providers_needed():
    check("providers: anthropic", isolation.providers_needed("anthropic/claude-haiku-4-5") == ["anthropic"])
    check("providers: openai", isolation.providers_needed("openai/gpt-5.4-mini") == ["openai"])
    check("providers: ollama none", isolation.providers_needed("ollama/silver-v8:e4b") == [])


if __name__ == "__main__":
    test_lock()
    test_teardown_dummies()
    test_run_opencode_timeout()
    test_run_opencode_stdout_is_pipe_backed()
    test_run_opencode_stages_placeholder_credentials_only()
    test_openai_proxy_owns_upstream_credential()
    test_start_proxies_scopes_credentials_to_provider_proxy()
    test_reset_hook()
    test_env_manifest()
    test_providers_needed()
    print(f"\nall {PASS} checks passed")
