#!/usr/bin/env python3
"""Move a results tree onto the variant layout: `results/<model>__<toolkit>@<version>/<task>/`.

One-time, per tree, idempotent. Every run's destination is read from its own `meta.json` stamp — the
same source `ledger.run_variant` and the exporter read — so the directory can never come to claim a
version the run does not carry. Runs with no version to record (the `none` control, pre-stamp legacy
runs) keep their bare toolkit directory and are left alone.

    python3 runner/migrate_variants.py --results results             # dry run: print the plan
    python3 runner/migrate_variants.py --results results --apply     # move
    python3 runner/migrate_variants.py --results results --apply --git   # move with `git mv`, staged

`results/ledger.json` is rewritten alongside, since its unit ids carry the variant too. The ledger is
derived from disk, so a tree whose ledger is missing or stale still migrates correctly.

See docs/tool-versions.md.
"""
import argparse
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger


def plan(results_dir):
    """[(src, dst)] run-directory moves, plus the runs that cannot be placed. Per RUN, not per cell:
    a cell holding two versions is a tree that pre-dates the layout, and it splits rather than moves."""
    moves, skipped = [], []
    for cell in sorted(os.listdir(results_dir)):
        cdir = os.path.join(results_dir, cell)
        if not os.path.isdir(cdir):
            continue
        for task in sorted(os.listdir(cdir)):
            rdir = os.path.join(cdir, task)
            if not os.path.isdir(rdir):
                continue
            # meta.json is written last, so its absence means the run is still in flight.
            meta = ledger._read_json(os.path.join(rdir, "meta.json"))
            if not meta or not meta.get("model") or not meta.get("tool"):
                skipped.append(rdir)
                continue
            target = ledger.cell_name(meta["model"], ledger.run_variant(meta))
            if target != cell:
                moves.append((rdir, os.path.join(results_dir, target, task)))
    return moves, skipped


def migrate_ledger(results_dir, apply_it):
    """Re-key the persisted units onto the variant ids. The variant of each unit is read off the run
    itself, not off the id it currently has, so this works the same before or after the moves. A unit
    with no result on disk keeps the id it has: there is nothing to derive a version from."""
    led = ledger._read_json(os.path.join(results_dir, ledger.LEDGER_NAME))
    if not led:
        return 0
    variants = {}                                   # (model, toolkit, task) -> variant
    for cell in sorted(os.listdir(results_dir)):
        cdir = os.path.join(results_dir, cell)
        if not os.path.isdir(cdir):
            continue
        for task in sorted(os.listdir(cdir)):
            meta = ledger._read_json(os.path.join(cdir, task, "meta.json")) or {}
            if meta.get("model") and meta.get("tool") and meta.get("task"):
                variants[(meta["model"], meta["tool"], meta["task"])] = ledger.run_variant(meta)
    units, rekeyed = {}, 0
    for uid, e in led.get("units", {}).items():
        model, task = e.get("model"), e.get("task")
        held = e.get("variant") or e.get("tool")
        if not (model and task and held):
            units[uid] = e
            continue
        variant = variants.get((model, ledger.split_variant(held)[0], task), held)
        new_uid = ledger.unit_id(model, variant, task)
        rekeyed += new_uid != uid
        entry = {"model": model, "variant": variant, "task": task, "state": e.get("state", "pending")}
        if e.get("verdict"):
            entry["verdict"] = e["verdict"]
        units[new_uid] = entry
    if apply_it:
        ledger.save(results_dir, {**led, "units": units})
    return rekeyed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "results"))
    ap.add_argument("--apply", action="store_true", help="perform the moves (default: dry run)")
    ap.add_argument("--git", action="store_true", help="move with `git mv`, leaving them staged")
    a = ap.parse_args()

    moves, skipped = plan(a.results)
    cells = {}
    for src, dst in moves:
        cells.setdefault((os.path.basename(os.path.dirname(src)),
                          os.path.basename(os.path.dirname(dst))), []).append(src)
    for (src_cell, dst_cell), runs in sorted(cells.items()):
        print(f"  {src_cell}  ->  {dst_cell}   ({len(runs)} runs)")
    if skipped:
        print(f"  ({len(skipped)} run(s) left in place: no meta.json yet, or no model/tool recorded)")
    if not moves:
        print("  nothing to move — this tree is already on the variant layout")

    collisions = [dst for _, dst in moves if os.path.exists(dst)]
    if collisions:
        print(f"\nREFUSING: {len(collisions)} destination(s) already exist, e.g. {collisions[0]}")
        return 1

    if not a.apply:
        print(f"\ndry run: {len(moves)} run(s) would move. Re-run with --apply.")
        return 0

    for src, dst in moves:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if a.git:
            subprocess.run(["git", "mv", src, dst], check=True)
        else:
            os.rename(src, dst)
    for cell in sorted(os.listdir(a.results)):
        cdir = os.path.join(a.results, cell)
        if os.path.isdir(cdir) and not os.listdir(cdir):
            os.rmdir(cdir)
    rekeyed = migrate_ledger(a.results, True)
    print(f"\nmoved {len(moves)} run(s); re-keyed {rekeyed} ledger unit(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
