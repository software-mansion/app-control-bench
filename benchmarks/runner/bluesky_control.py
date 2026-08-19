#!/usr/bin/env python3
"""Fail-closed Bluesky backend and fresh-clone session checks."""
import json
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request

import bench_env


CONTROL_URL = os.environ.get("BENCH_BSKY_CONTROL_URL", "http://127.0.0.1:1987")
BSKY_BUNDLE = "xyz.blueskyweb.app"
LOGGED_OUT_MARKERS = (
    "sign in",
    "create account",
    "search is unavailable while logged out",
)
FIRST_POST_TEXT = "Mochi napping in a sunbeam 🐱 #caturday"
MUTATION_SPECS = {
    "bsky-25": {"kind": "reply", "exact_text": "nice one"},
    "bsky-28": {"kind": "post", "exact_text": "hello from the benchmark"},
    "bsky-29": {"kind": "post", "exact_text": "testing hashtags #silverbench"},
    "bsky-30": {"kind": "quote", "exact_text": "sharing this"},
}


class BlueskyControlError(RuntimeError):
    pass


def _json_get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read().decode() or "{}")
    except Exception as exc:
        raise BlueskyControlError(f"Bluesky control request failed: {exc}") from exc


def backend_identity():
    info = _json_get(CONTROL_URL + "/info")
    identity = {
        "pds_url": info.get("pdsUrl"),
        "appview_did": info.get("appviewDid"),
        "bench_did": (info.get("bench") or {}).get("did"),
    }
    if not all(identity.values()):
        raise BlueskyControlError("Bluesky /info is missing pdsUrl, appviewDid, or bench.did")
    return identity


def _list_records(pds_url, did):
    records = []
    cursor = None
    while True:
        query = {"repo": did, "collection": "app.bsky.feed.post", "limit": "100"}
        if cursor:
            query["cursor"] = cursor
        page = _json_get(
            pds_url.rstrip("/")
            + "/xrpc/com.atproto.repo.listRecords?"
            + urllib.parse.urlencode(query)
        )
        records.extend(page.get("records") or [])
        cursor = page.get("cursor")
        if not cursor:
            return records


def _quoted_uri(value):
    embed = value.get("embed") or {}
    if embed.get("$type") == "app.bsky.embed.record":
        return (embed.get("record") or {}).get("uri")
    if embed.get("$type") == "app.bsky.embed.recordWithMedia":
        return ((embed.get("record") or {}).get("record") or {}).get("uri")
    return None


def evaluate_mutation_postcondition(task_id, bench_records, target_uri):
    spec = MUTATION_SPECS[task_id]

    def relation(value):
        reply = value.get("reply") or {}
        if reply:
            return "reply", (reply.get("parent") or {}).get("uri")
        quote_uri = _quoted_uri(value)
        if quote_uri:
            return "quote", quote_uri
        return "post", None

    exact_text_records = [
        record for record in bench_records if (record.get("value") or {}).get("text") == spec["exact_text"]
    ]
    matched = None
    for record in exact_text_records:
        record_kind, record_target = relation(record.get("value") or {})
        target_matches = record_kind == "post" or record_target == target_uri
        if record_kind == spec["kind"] and target_matches:
            matched = record
            break
    expected = {"exact_text": spec["exact_text"], "relation": spec["kind"]}
    if spec["kind"] in ("reply", "quote"):
        expected["target_uri"] = target_uri
    observed = None
    if matched:
        value = matched.get("value") or {}
        record_kind, record_target = relation(value)
        observed = {
            "uri": matched.get("uri"),
            "text": value.get("text"),
            "relation": record_kind,
        }
        if record_target:
            observed["target_uri"] = record_target
    else:
        observed = {
            "exact_text_candidates": len(exact_text_records),
            "relations": sorted({relation(record.get("value") or {})[0] for record in exact_text_records}),
        }
    return {
        "schema": "bluesky-postcondition/v1",
        "source": "atproto-repo",
        "task": task_id,
        "kind": spec["kind"],
        "passed": matched is not None,
        "expected": expected,
        "observed": observed,
    }


def assert_mutation_postcondition(task_id):
    """Read authoritative PDS records for a mutation task; returns None for other tasks."""
    if task_id not in MUTATION_SPECS:
        return None
    info = _json_get(CONTROL_URL + "/info")
    pds_url = info.get("pdsUrl")
    bench_did = (info.get("bench") or {}).get("did")
    whiskers_did = (info.get("accounts") or {}).get("whiskers.test")
    if not all((pds_url, bench_did, whiskers_did)):
        raise BlueskyControlError("Bluesky /info is missing PDS, bench DID, or whiskers DID")
    target = next(
        (
            record.get("uri")
            for record in _list_records(pds_url, whiskers_did)
            if (record.get("value") or {}).get("text") == FIRST_POST_TEXT
        ),
        None,
    )
    if not target:
        raise BlueskyControlError("seeded first whiskers post is missing from the PDS")
    return evaluate_mutation_postcondition(task_id, _list_records(pds_url, bench_did), target)


def _flatten_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _flatten_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _flatten_strings(item)


def validate_clone_session(udid, state_dir):
    """Prove the specified fresh clone is showing an authenticated Following feed."""
    adev = bench_env.agent_device()
    session = f"bench-auth-{os.getpid()}-{udid[-8:]}"
    common = ["--platform", "ios", "--udid", udid, "--session", session]
    state_parent = os.path.dirname(os.path.abspath(state_dir))
    # Validation runs for every tool mode, so it cannot share the benchmark tool's daemon lifecycle.
    with tempfile.TemporaryDirectory(
        prefix="bluesky-validation-", dir=state_parent
    ) as validation_state_dir:
        env = {**os.environ, "AGENT_DEVICE_STATE_DIR": validation_state_dir}
        try:
            opened = subprocess.run(
                [adev, "open", BSKY_BUNDLE, *common],
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            if opened.returncode != 0:
                raise BlueskyControlError(
                    f"could not inspect Bluesky on fresh clone {udid}: {(opened.stderr or '')[-300:]}"
                )
            captured = subprocess.run(
                [adev, "snapshot", "-i", "-c", "--json", *common],
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
            if captured.returncode != 0:
                raise BlueskyControlError(
                    f"could not capture Bluesky app state on fresh clone {udid}: {(captured.stderr or '')[-300:]}"
                )
            try:
                payload = json.loads(captured.stdout or "{}")
            except json.JSONDecodeError as exc:
                raise BlueskyControlError("Bluesky clone app-state check returned invalid JSON") from exc
            strings = [value.strip() for value in _flatten_strings(payload) if value.strip()]
            folded = "\n".join(strings).casefold()
            logged_out = [marker for marker in LOGGED_OUT_MARKERS if marker in folded]
            has_feed_identifier = "followingfeedpage" in folded
            has_feed_content = "following" in folded and any(
                handle in folded for handle in ("whiskers.test", "rex.test", "mittens.test", "buddy.test")
            )
            if logged_out or not (has_feed_identifier or has_feed_content):
                reason = (
                    f"logged-out marker {logged_out[0]!r}"
                    if logged_out
                    else "Following feed evidence missing"
                )
                raise BlueskyControlError(f"fresh clone {udid} is not authenticated: {reason}")
            return {
                "schema": "bluesky-clone-session/v1",
                "clone_udid": udid,
                "authenticated": True,
                "evidence": "followingFeedPage" if has_feed_identifier else "Following feed with seeded account",
            }
        finally:
            try:
                subprocess.run(
                    [adev, "close", *common], capture_output=True, text=True, timeout=60, env=env
                )
            finally:
                subprocess.run(
                    [adev, "daemon", "stop", "--state-dir", validation_state_dir],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=env,
                )
