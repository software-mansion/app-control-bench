#!/usr/bin/env python3
"""Local unit tests for editions: the config, the runner's wiring to the current edition, the
exporter's one-dataset-per-edition output, the judge's request, and the publish gate's path shapes -
no devices, no network, no API spend.

    python3 runner/test_editions.py
"""
import json
import os
import sys
import tempfile
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench
import bench_models
import editions
import judge
import report_data
import run_matrix
from test_preload import TASK, fresh, harness, patched

PASS = 0
NO_PRELOAD = {"none": {"why": "t", "steps": [], "check": None}}


def check(name, cond, detail=""):
    global PASS
    if not cond:
        print(f"FAIL {name} {detail}")
        sys.exit(1)
    PASS += 1
    print(f"ok   {name}")


def write_config(d, cfg):
    path = os.path.join(d, "editions.json")
    json.dump(cfg, open(path, "w"))
    return path


def test_config():
    cfg = editions.load()
    cur = editions.current(cfg)
    check("the config names a current edition, listed first", editions.all_editions(cfg)[0]["id"] == cur["id"], cur)
    check("the current edition has a results directory of its own",
          [e["id"] for e in editions.by_directory(cfg)[editions.results_dir(cur)]] == [cur["id"]])
    check("... a harness, an OpenCode version, a judge and a roster",
          all(cur.get(k) for k in ("harness", "opencode", "judge", "models")), cur)
    shared = [es for es in editions.by_directory(cfg).values() if len(es) > 1]
    check("editions that share a directory are told apart by harness, with one catch-all at most",
          all(sum(1 for e in es if not e.get("harness")) <= 1 for es in shared), shared)


def test_config_is_validated():
    base = {"id": "a", "label": "A", "results": "results/editions/a", "harness": "h1"}
    with tempfile.TemporaryDirectory() as d:
        for name, cfg in (
            ("a current edition that is not listed", {"current": "b", "editions": [base]}),
            ("duplicate ids", {"current": "a", "editions": [base, dict(base, harness="h2")]}),
            ("two editions claiming one directory and harness",
             {"current": "a", "editions": [base, dict(base, id="b")]}),
            ("an edition without a results directory", {"current": "a", "editions": [dict(base, results="")]}),
        ):
            try:
                editions.load(write_config(d, cfg))
                check(f"load() refuses {name}", False)
            except ValueError:
                check(f"load() refuses {name}", True)


def test_assignment():
    v3, legacy = {"id": "v3", "harness": "isolation-v3"}, {"id": "old", "harness": None}
    check("a run goes to the edition naming its harness", editions.of("isolation-v3", [v3, legacy]) is v3)
    check("... else to the directory's catch-all", editions.of(None, [v3, legacy]) is legacy
          and editions.of("isolation-v1", [v3, legacy]) is legacy)
    check("... else nowhere", editions.of("isolation-v1", [v3]) is None)


def test_runner_is_wired_to_the_current_edition():
    cur = editions.current()
    check("bench writes into the current edition's directory", bench.RESULTS == editions.results_dir(cur), bench.RESULTS)
    check("bench.HARNESS is the current edition's harness", bench.HARNESS == cur["harness"],
          f"{bench.HARNESS} != {cur['harness']}: a harness change starts a new edition")
    check("the pinned OpenCode is the current edition's", bench.pinned_opencode_version() == cur["opencode"],
          f"{bench.pinned_opencode_version()} != {cur['opencode']}")
    check("the judge is the current edition's", judge.JUDGE_MODEL == cur["judge"] and judge.RESULTS == bench.RESULTS)
    check("a bare matrix runs the current edition's roster", run_matrix.PLAN == cur["models"], run_matrix.PLAN)
    for m in cur["models"]:
        check(f"{m}: runnable, with an effort, and on the report roster",
              m in bench.MODELS and bench.EFFORT.get(m) and m in bench_models.MODELS and m in bench_models.LABELS)
    ids = [bench.MODELS[m] for m in cur["models"]]
    check("no roster key shares a checkpoint and effort with another",
          len({(bench.MODELS[m], json.dumps(bench.EFFORT[m], sort_keys=True)) for m in cur["models"]}) == len(ids))


def test_matrix_stays_on_the_roster():
    import isolation, sim_device
    from test_preload import environ
    ran = []
    common = dict(run_one=lambda key, tool, t, force=False: ran.append(key), tool_variant=lambda tool: tool,
                  load_tasks=lambda: [dict(TASK)])
    with tempfile.TemporaryDirectory() as results, patched(bench, RESULTS=results, **common), \
            patched(isolation, acquire_lock=lambda **k: None), patched(sim_device, check_golden=lambda: {}):
        with environ(ONLY="gpt_low", TOOLS="none", APPS="bluesky", TASK=None):
            try:
                run_matrix.main()
                check("ONLY naming a model outside the edition's roster is refused", False)
            except SystemExit as e:
                check("ONLY naming a model outside the edition's roster is refused",
                      "gpt_low" in str(e) and "editions.json" in str(e) and ran == [], e)
        with environ(ONLY=None, TOOLS=None, APPS=None, TASK=None), tempfile.TemporaryDirectory() as d:
            queue = os.path.join(d, "queue.json")
            on = bench.EDITION["models"][0]
            json.dump({"entries": [
                {"model": "gpt_low", "tool": "none", "apps": ["bluesky"], "enabled": True, "priority": 0},
                {"model": on, "tool": "none", "apps": ["bluesky"], "enabled": True, "priority": 1}]}, open(queue, "w"))
            with patched(run_matrix, QUEUE_PATH=queue):
                run_matrix.main()
            check("a queue entry for a model outside the roster is skipped, the rest run", ran == [on], ran)


def test_run_one_stamps_the_edition():
    with tempfile.TemporaryDirectory() as results, fresh(NO_PRELOAD), harness(results):
        meta = bench.run_one("gpt_low", "none", TASK)
        check("meta.json versions.edition is the current edition",
              meta["versions"].get("edition") == editions.current()["id"], meta["versions"])


def put_run(root, cell, task, model, tool, harness_stamp, verdict="success", version=None):
    rdir = os.path.join(root, cell, task)
    os.makedirs(rdir)
    versions = {"schema": 2, "tool": version}
    if harness_stamp:
        versions["harness"] = harness_stamp
    json.dump({"model": model, "tool": tool, "task": task, "returncode": 0, "wall_s": 1.0, "versions": versions},
              open(os.path.join(rdir, "meta.json"), "w"))
    if verdict:
        json.dump({"verdict": verdict, "judge_model": "j"}, open(os.path.join(rdir, "score.json"), "w"))


def fake_tree(d):
    """A checkout with a bare tree shared by two past editions and one edition directory, plus the
    config that says so. Returns (root, cfg)."""
    cfg = {"current": "new", "editions": [
        {"id": "new", "label": "New", "results": "results/editions/new", "harness": "h5", "opencode": "2",
         "judge": "jn", "models": ["haiku55_low", "haiku55_high"]},
        {"id": "mid", "label": "Mid", "results": "results", "harness": "h3", "opencode": "1", "judge": "jo"},
        {"id": "old", "label": "Old", "results": "results", "harness": None},
    ]}
    legacy, new = os.path.join(d, "results"), os.path.join(d, "results", "editions", "new")
    put_run(legacy, "gpt_low__argent@1.0.0", "bsky-01", "gpt_low", "argent", "h3", version="1.0.0")
    put_run(legacy, "gpt_low__none", "bsky-01", "gpt_low", "none", "h3", verdict="fail")
    put_run(legacy, "gpt_high__argent", "bsky-01", "gpt_high", "argent", None)
    put_run(legacy, "gpt_high__argent@0.9.0", "bsky-02", "gpt_high", "argent", "h1", version="0.9.0")
    put_run(new, "haiku55_low__argent@2.0.0", "bsky-01", "haiku55_low", "argent", "h5", version="2.0.0")
    put_run(new, "haiku55_low__argent@2.0.0", "bsky-02", "haiku55_low", "argent", "h4", version="2.0.0")
    return d, cfg


def test_exporter_partitions_runs_by_edition():
    with tempfile.TemporaryDirectory() as d:
        root, cfg = fake_tree(d)
        rows = report_data.edition_rows(root, cfg)
        check("every run lands in exactly one edition",
              {k: sorted(v) for k, v in rows.items()} ==
              {"new": [("haiku55_low", "argent@2.0.0", "bsky-01"), ("haiku55_low", "argent@2.0.0", "bsky-02")],
               "mid": [("gpt_low", "argent@1.0.0", "bsky-01"), ("gpt_low", "none", "bsky-01")],
               "old": [("gpt_high", "argent", "bsky-01"), ("gpt_high", "argent@0.9.0", "bsky-02")]},
              {k: sorted(v) for k, v in rows.items()})
        check("a run carrying its edition's harness is current there, whatever bench.HARNESS is now",
              not any(r["polluted"] for r in rows["mid"].values()))
        check("a catch-all's runs are never scored", all(r["polluted"] for r in rows["old"].values()))
        check("a run in an edition's directory under another harness is stale in it",
              [r["polluted"] for _k, r in sorted(rows["new"].items())] == [False, True])


def test_exporter_emits_one_dataset_per_edition():
    with tempfile.TemporaryDirectory() as d:
        root, cfg = fake_tree(d)
        with patched(report_data.editions_config, load=lambda path=None: cfg):
            datasets = report_data.gather_editions(root, cfg)
            by_id = {ds.edition["id"]: ds for ds in datasets}
            check("editions come newest first", [ds.edition["id"] for ds in datasets] == ["new", "mid", "old"])
            check("the current edition shows its whole roster, pending models included",
                  by_id["new"].models == ["haiku55_low", "haiku55_high"], by_id["new"].models)
            check("a past edition shows only the models it ran", by_id["mid"].models == ["gpt_low"]
                  and by_id["old"].models == ["gpt_high"], (by_id["mid"].models, by_id["old"].models))
            check("... and only the tool versions it ran",
                  by_id["mid"].variants == [("argent", "1.0.0"), ("none", None)], by_id["mid"].variants)
            check("the current edition keeps a pending column for a toolkit it has not run yet",
                  {t for t, _v in by_id["new"].variants} == set(report_data.TOOLKITS), by_id["new"].variants)
            check("the site opens on the newest edition with a scored run",
                  report_data.default_edition_id(datasets) == "new")
            only_stale = [ds for ds in datasets if ds.edition["id"] != "mid"]
            for row in by_id["new"].rows.values():
                row["polluted"] = True
            check("... skipping one that has none yet", report_data.default_edition_id(datasets) == "mid")
            check("... and falling back to the newest", report_data.default_edition_id(only_stale) == "new")

            catalog = report_data.build_catalog(datasets)
            check("editions.json lists them and names the default",
                  [(e["id"], e["current"], e["ranked"]) for e in catalog["editions"]] ==
                  [("new", True, True), ("mid", False, True), ("old", False, False)]
                  and catalog["defaultEditionId"] == "mid", catalog)
            resources, _shots = report_data.build_resources(by_id["mid"], "now", "b1", catalog)
            base = "data/v1/editions/mid"
            check("an edition's resources live under its own directory",
                  {f"{base}/run-index.json", f"{base}/report-meta.json",
                   f"{base}/runs/gpt_low__argent@1.0.0__bsky-01.json"} <= set(resources)
                  and all(k.startswith(base + "/") for k in resources), sorted(resources)[:4])
            manifest = resources[f"{base}/report-meta.json"]["manifest"]
            check("... and its manifest points every page at them and at the other editions",
                  (manifest["dataRoot"], manifest["artifactRoot"], manifest["edition"]["id"],
                   manifest["defaultEditionId"], [e["id"] for e in manifest["editions"]]) ==
                  ("/data/v1/editions/mid", "/artifacts/mid", "mid", "mid", ["new", "mid", "old"]), manifest)
            cells = resources[f"{base}/run-index.json"]["cells"]
            check("a past edition's run index holds its own runs, scored, and nobody else's",
                  {c["modelId"] for c in cells} == {"gpt_low"}
                  and sorted(c["lifecycle"] for c in cells if c["taskId"] == "bsky-01") == ["judged", "judged"], cells[:3])


def test_an_empty_tree_still_exports_the_current_edition():
    with tempfile.TemporaryDirectory() as d:
        cfg = {"current": "new", "editions": [
            {"id": "new", "label": "New", "results": "results/editions/new", "harness": "h5",
             "models": ["haiku55_low"]},
            {"id": "mid", "label": "Mid", "results": "results", "harness": "h3"}]}
        with patched(report_data.editions_config, load=lambda path=None: cfg):
            datasets = report_data.gather_editions(d, cfg)
        check("an edition with no runs is left out, except the current one when none has any",
              [ds.edition["id"] for ds in datasets] == ["new"] and datasets[0].models == ["haiku55_low"])


def test_judge_request():
    body = judge.vision_body("p", "QUJD", "some-model")
    check("the judge asks at temperature 0", body["temperature"] == 0 and body["model"] == "some-model")
    sent = []

    def post(body):
        sent.append(dict(body))
        if "temperature" in body:
            raise urllib.error.HTTPError("u", 400, "Bad Request", {}, __import__("io").BytesIO(
                b'{"error": {"message": "Unsupported value: temperature does not support 0 with this model"}}'))
        return {"choices": [{"message": {"content": '{"verdict":"success"}'}}]}

    with tempfile.NamedTemporaryFile(suffix=".png") as png, patched(judge, _NO_TEMPERATURE=set()):
        png.write(b"x")
        png.flush()
        out = judge.call_vision("p", png.name, "picky-model", post=post)
        check("a model that rejects temperature is asked again without it, at once",
              out == '{"verdict":"success"}' and ["temperature" in b for b in sent] == [True, False], sent)
        judge.call_vision("p", png.name, "picky-model", post=post)
        check("... and not sent it again", ["temperature" in b for b in sent] == [True, False, False])


def test_publish_gate_understands_both_layouts():
    try:
        import publish
    except ImportError as e:        # PyYAML is a release-time dependency, not a runner one
        print(f"skip publish gate ({e})")
        return
    flat = "results/gpt_low__argent@0.15.0/bsky-01/meta.json"
    nested = "results/editions/2026-11/haiku55_low__argent@0.33.0/bsky-01/meta.json"
    check("a run in the bare tree is a run", publish.run_parts(flat) == ["gpt_low__argent@0.15.0", "bsky-01", "meta.json"])
    check("a run in an edition's tree is a run, gated on the same cell",
          publish.run_parts(nested) == ["haiku55_low__argent@0.33.0", "bsky-01", "meta.json"]
          and publish.model_of(publish.cell_of(nested)) == "haiku55_low")
    for path in ("results/ledger.json", "results/editions/2026-11/ledger.json", "results/editions/2026-11",
                 "results/editions/2026-11/x__y/bsky-01/deep/file", "results/x__y/bsky-01"):
        check(f"{path} is not a run", publish.run_parts(path) is None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print(f"\n{PASS} checks passed")
