#!/usr/bin/env python3
"""Local unit tests for ledger.py's variant layout — no devices, no network, no API spend.
Every case runs against a throwaway results tree under tempfile.

    python3 runner/test_ledger.py
"""
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger

PASS = 0


def check(name, cond, detail=""):
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok   {name}")


def write_run(root, cell, task, tool, version, verdict="success"):
    """A run complete enough for disk_state: meta.json, final.png, and a verdict."""
    rdir = os.path.join(root, cell, task)
    os.makedirs(rdir, exist_ok=True)
    meta = {"cell": f"m:{tool}", "model": "m", "tool": tool, "task": task, "returncode": 0,
            "versions": {"schema": 2, "harness": "h", "tool": version}}
    json.dump(meta, open(os.path.join(rdir, "meta.json"), "w"))
    open(os.path.join(rdir, "final.png"), "wb").write(b"\x89PNG\r\n\x1a\n")
    if verdict:
        json.dump({"verdict": verdict}, open(os.path.join(rdir, "score.json"), "w"))
    return rdir


def test_variant_id():
    check("variant_id: versioned", ledger.variant_id("argent", "0.15.0") == "argent@0.15.0")
    check("variant_id: no version is the bare toolkit", ledger.variant_id("none", None) == "none")
    check("variant_id: empty version is the bare toolkit", ledger.variant_id("argent", "") == "argent")
    check("split_variant: versioned", ledger.split_variant("argent@0.15.0") == ("argent", "0.15.0"))
    check("split_variant: bare", ledger.split_variant("none") == ("none", None))
    check("unit_id carries the version", ledger.unit_id("m", "argent@0.15.0", "t") == "m__argent@0.15.0__t")


def test_run_variant():
    check("run_variant: from the run's own stamp",
          ledger.run_variant({"tool": "argent", "versions": {"tool": "0.21.0"}}) == "argent@0.21.0")
    check("run_variant: control has no version to borrow",
          ledger.run_variant({"tool": "none", "versions": {"tool": None}}) == "none")
    check("run_variant: pre-stamp legacy run stays bare",
          ledger.run_variant({"tool": "argent"}) == "argent")


def test_versions_coexist():
    """The point of the layout: a second version lands beside the first, not on top of it."""
    with tempfile.TemporaryDirectory() as root:
        write_run(root, "m__argent@0.15.0", "t1", "argent", "0.15.0", "success")
        write_run(root, "m__argent@0.21.0", "t1", "argent", "0.21.0", "fail")
        check("coexist: both versions are judged units",
              ledger.disk_state(root, "m", "argent@0.15.0", "t1") == "judged"
              and ledger.disk_state(root, "m", "argent@0.21.0", "t1") == "judged")
        check("coexist: distinct directories",
              ledger.result_dir(root, "m", "argent@0.15.0", "t1")
              != ledger.result_dir(root, "m", "argent@0.21.0", "t1"))
        led = ledger.reconcile(root, ["m"], ["argent@0.15.0", "argent@0.21.0"], ["t1"], persist=False)
        check("coexist: one ledger unit per variant", len(led) == 2, led)
        check("coexist: verdicts do not cross",
              led["m__argent@0.15.0__t1"]["verdict"] == "success"
              and led["m__argent@0.21.0__t1"]["verdict"] == "fail")


def test_legacy_fallback():
    """An un-migrated tree names the directory after the bare toolkit. Its runs must still be found,
    or the next matrix regenerates every one of them."""
    with tempfile.TemporaryDirectory() as root:
        legacy = write_run(root, "m__argent", "t1", "argent", "0.15.0")
        check("fallback: bare dir found for the version it holds",
              ledger.result_dir(root, "m", "argent@0.15.0", "t1") == legacy)
        check("fallback: that unit reads as judged, not pending",
              ledger.disk_state(root, "m", "argent@0.15.0", "t1") == "judged")
        check("fallback: needs_run is False at the matching harness",
              ledger.needs_run(root, "m", "argent@0.15.0", "t1", "h") is False)
        check("fallback: a DIFFERENT version does not borrow the bare dir",
              ledger.disk_state(root, "m", "argent@0.21.0", "t1") == "pending")
        check("fallback: nor does a different toolkit",
              ledger.disk_state(root, "m", "agent-device@0.15.0", "t1") == "pending")


def test_migrated_wins():
    """With both layouts present the versioned directory is the answer; the bare one is only a fallback."""
    with tempfile.TemporaryDirectory() as root:
        write_run(root, "m__argent", "t1", "argent", "0.15.0", "fail")
        migrated = write_run(root, "m__argent@0.15.0", "t1", "argent", "0.15.0", "success")
        check("migrated dir wins over the bare one",
              ledger.result_dir(root, "m", "argent@0.15.0", "t1") == migrated)
        led = ledger.reconcile(root, ["m"], ["argent@0.15.0"], ["t1"], persist=False)
        check("migrated verdict is the one recorded",
              led["m__argent@0.15.0__t1"]["verdict"] == "success")


def test_control_needs_no_version():
    with tempfile.TemporaryDirectory() as root:
        rdir = write_run(root, "m__none", "t1", "none", None)
        check("control: bare id resolves to the bare dir",
              ledger.result_dir(root, "m", "none", "t1") == rdir)
        check("control: judged", ledger.disk_state(root, "m", "none", "t1") == "judged")


def test_phantoms_keyed_by_variant():
    with tempfile.TemporaryDirectory() as root:
        write_run(root, "m__argent@0.15.0", "t1", "argent", "0.15.0")
        ledger.reconcile(root, ["m"], ["argent@0.15.0"], ["t1"])
        check("phantoms: none while the result is on disk",
              ledger.phantoms(root, ["m"], ["argent@0.15.0"], ["t1"]) == [])
        os.remove(os.path.join(root, "m__argent@0.15.0", "t1", "meta.json"))
        check("phantoms: the purged unit is named by its variant",
              ledger.phantoms(root, ["m"], ["argent@0.15.0"], ["t1"]) == ["m__argent@0.15.0__t1"])


def current_harness():
    """bench.HARNESS, read from source: importing bench shells out to detect the tools."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bench.py")).read()
    return re.search(r'^HARNESS = "([^"]+)"', src, re.M).group(1)


def test_harness_stamp():
    here = os.path.dirname(os.path.abspath(__file__))
    cur = current_harness()
    # judge, report and report_data fall back to a literal copy when importing bench fails
    for name in ("judge.py", "report.py", "report_data.py"):
        src = open(os.path.join(here, name)).read()
        copy = re.search(r'^\s+(?:CUR|CURRENT)_HARNESS = "([^"]+)"', src, re.M).group(1)
        check(f"harness: {name}'s fallback is bench.HARNESS", copy == cur, f"{copy} != {cur}")
    with tempfile.TemporaryDirectory() as root:
        rdir = write_run(root, "m__argent@0.15.0", "t1", "argent", "0.15.0")
        meta = json.load(open(os.path.join(rdir, "meta.json")))
        meta["versions"]["harness"] = "isolation-v3-progressive"
        json.dump(meta, open(os.path.join(rdir, "meta.json"), "w"))
        check("harness: a v3 run is stale under the current harness",
              ledger.needs_run(root, "m", "argent@0.15.0", "t1", cur))
        meta["versions"]["harness"] = cur
        json.dump(meta, open(os.path.join(rdir, "meta.json"), "w"))
        check("harness: a current-harness run is not",
              not ledger.needs_run(root, "m", "argent@0.15.0", "t1", cur))


if __name__ == "__main__":
    test_variant_id()
    test_run_variant()
    test_versions_coexist()
    test_legacy_fallback()
    test_migrated_wins()
    test_control_needs_no_version()
    test_phantoms_keyed_by_variant()
    test_harness_stamp()
    print(f"\nall {PASS} checks passed")
