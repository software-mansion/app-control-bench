#!/usr/bin/env python3
"""Benchmark editions (configs/editions.json): which runs may be compared with which.

An edition is one frozen setup - a harness generation, an OpenCode version, a model roster and a judge.
Two results are comparable only inside one edition: the same tool version scored under another harness,
on other models or by another judge is a different measurement. So the runner writes each edition into
its own results directory and stamps every run with it, and the site shows one edition at a time.

  current()            the edition new runs belong to; results_dir(current()) is bench.RESULTS
  assign(roots)        which edition each run of a results directory belongs to (see `of`)

Results written before editions existed all sit in the bare `results/` tree. They are not moved: the
editions that name that directory share it, and a run there belongs to the edition whose `harness` its
own stamp carries, or else to the one with `harness: null`. docs/editions.md.
"""
import json, os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "configs", "editions.json")


def load(path=None):
    """The parsed config, validated: unique ids, a `current` that exists, at most one catch-all per
    results directory, and no directory shared by two editions with the same harness."""
    with open(path or CONFIG) as f:
        cfg = json.load(f)
    editions = cfg.get("editions") or []
    ids = [e.get("id") for e in editions]
    if not ids or len(set(ids)) != len(ids) or not all(ids):
        raise ValueError(f"{CONFIG}: editions need unique ids, got {ids}")
    if cfg.get("current") not in ids:
        raise ValueError(f"{CONFIG}: `current` is {cfg.get('current')!r}, which is not one of {ids}")
    seen = set()
    for e in editions:
        if not e.get("results") or not e.get("label"):
            raise ValueError(f"{CONFIG}: edition {e['id']} needs `results` and `label`")
        key = (os.path.normpath(e["results"]), e.get("harness"))
        if key in seen:
            raise ValueError(f"{CONFIG}: two editions claim {key[0]} with harness {key[1]!r}")
        seen.add(key)
    return cfg


def all_editions(cfg=None):
    """Every edition, newest first (the order of the file)."""
    return list((cfg or load())["editions"])


def get(edition_id, cfg=None):
    for e in all_editions(cfg):
        if e["id"] == edition_id:
            return e
    raise KeyError(f"no edition {edition_id!r} in {CONFIG}")


def current(cfg=None):
    cfg = cfg or load()
    return get(cfg["current"], cfg)


def results_dir(edition, root=None):
    """Absolute results directory of an edition. `root` re-bases it, for an exporter or a test pointed
    at another checkout."""
    return os.path.normpath(os.path.join(root or ROOT, edition["results"]))


def of(harness, sharing):
    """The edition, among those `sharing` one results directory, that a run stamped `harness` belongs
    to: the one that names that harness, else the directory's catch-all (`harness: null`), else None."""
    for e in sharing:
        if e.get("harness") and e["harness"] == harness:
            return e
    return next((e for e in sharing if not e.get("harness")), None)


def by_directory(cfg=None, root=None):
    """{results directory: [the editions that share it]}, in file order."""
    out = {}
    for e in all_editions(cfg):
        out.setdefault(results_dir(e, root), []).append(e)
    return out
