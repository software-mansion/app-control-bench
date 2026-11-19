#!/usr/bin/env python3
"""
Upload the generated library into Immich and organise it so all Immich benchmark
tasks are runnable. Idempotent — safe to re-run (checksum dedup + stable ids).

Steps: login -> upload every asset -> create "Summer Vacation" + "City Trips"
albums -> mark favorites -> build "on this day" memories -> kick metadata +
thumbnail jobs (reverse-geocoding for Places runs inside metadata extraction).

Env overrides: IMMICH_URL (default http://localhost:2283),
IMMICH_EMAIL / IMMICH_PASSWORD (default admin@immich.local / admin1234).
Uses only the stdlib + curl, so any python3 works.
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "assets.manifest.json")
ASSETS = os.path.join(HERE, "assets")

BASE = os.environ.get("IMMICH_URL", "http://localhost:2283").rstrip("/")
EMAIL = os.environ.get("IMMICH_EMAIL", "admin@immich.local")
PASSWORD = os.environ.get("IMMICH_PASSWORD", "admin1234")

SUMMER_ALBUM = "Summer Vacation"
CITY_ALBUM = "City Trips"


def api(method, path, token=None, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        raise SystemExit(f"API {method} {path} -> {e.code}: {detail}")


def upload(file_path, token, taken_iso, is_favorite):
    """POST /api/assets via curl (multipart). Returns (id, status)."""
    sha1 = hashlib.sha1(open(file_path, "rb").read()).hexdigest()
    dev_asset_id = os.path.basename(file_path)  # stable -> dedup on re-run
    ts = taken_iso.split(".")[0].replace("+00:00", "") + ".000Z"
    out = subprocess.run([
        "curl", "-s", "-X", "POST", f"{BASE}/api/assets",
        "-H", f"Authorization: Bearer {token}",
        "-H", f"x-immich-checksum: {sha1}",
        "-F", f"deviceAssetId={dev_asset_id}",
        "-F", "deviceId=acb-seed",
        "-F", f"fileCreatedAt={ts}",
        "-F", f"fileModifiedAt={ts}",
        "-F", f"isFavorite={'true' if is_favorite else 'false'}",
        "-F", f"assetData=@{file_path}",
    ], capture_output=True, text=True)
    try:
        d = json.loads(out.stdout)
        return d.get("id"), d.get("status")
    except Exception:
        raise SystemExit(f"upload {dev_asset_id} failed: {out.stdout[:200]} {out.stderr[:200]}")


def ensure_album(name, asset_ids, token, existing):
    if name in existing:
        aid = existing[name]
        if asset_ids:
            api("PUT", f"/api/albums/{aid}/assets", token, {"ids": asset_ids})
        return aid
    alb = api("POST", "/api/albums", token, {"albumName": name, "assetIds": asset_ids})
    return alb["id"]


def main():
    if not os.path.exists(MANIFEST):
        sys.exit("no manifest — run generate_assets.py first")
    manifest = json.load(open(MANIFEST))["assets"]

    print(f"==> login {EMAIL} @ {BASE}")
    token = api("POST", "/api/auth/login", body={"email": EMAIL, "password": PASSWORD})["accessToken"]

    print(f"==> uploading {len(manifest)} assets")
    id_by_file, created, dup = {}, 0, 0
    for m in manifest:
        fp = os.path.join(ASSETS, m["file"])
        aid, status = upload(fp, token, m["taken"], m["favorite"])
        id_by_file[m["file"]] = aid
        created += status == "created"
        dup += status == "duplicate"
    print(f"    created={created} duplicate={dup}")

    # albums
    existing = {a["albumName"]: a["id"] for a in api("GET", "/api/albums", token)}
    summer = [id_by_file[m["file"]] for m in manifest if m.get("album") == SUMMER_ALBUM]
    city = [id_by_file[m["file"]] for m in manifest if m.get("album") == CITY_ALBUM]
    ensure_album(SUMMER_ALBUM, summer, token, existing)
    ensure_album(CITY_ALBUM, city, token, existing)
    print(f"==> albums: '{SUMMER_ALBUM}'={len(summer)} '{CITY_ALBUM}'={len(city)}")

    # favorites (bulk)
    favs = [id_by_file[m["file"]] for m in manifest if m["favorite"]]
    if favs:
        api("PUT", "/api/assets", token, {"ids": favs, "isFavorite": True})
    print(f"==> favorites: {len(favs)}")

    # memories: one "on this day" per past year
    by_year = {}
    for m in manifest:
        if m.get("is_memory"):
            by_year.setdefault(m["memory_year"], []).append((m["taken"], id_by_file[m["file"]]))
    made = 0
    for year, items in sorted(by_year.items()):
        memory_at = items[0][0].split(".")[0] + ".000Z"
        try:
            api("POST", "/api/memories", token, {
                "type": "on_this_day", "data": {"year": year},
                "assetIds": [i for _, i in items], "memoryAt": memory_at,
            })
            made += 1
        except SystemExit as e:
            print(f"    (memory {year} skipped: {e})")
    print(f"==> memories created: {made}")

    # kick processing (metadata extraction also does reverse-geocoding for Places)
    for job in ("metadataExtraction", "thumbnailGeneration"):
        try:
            api("PUT", f"/api/jobs/{job}", token, {"command": "start", "force": False})
        except SystemExit as e:
            print(f"    (job {job}: {e})")
    print("==> queued metadataExtraction + thumbnailGeneration")

    stats = api("GET", "/api/assets/statistics", token)
    print(f"\nSEED DONE. assets: {json.dumps(stats)}")
    print("  Verify Places/People after metadata + (optional) ML jobs finish.")


if __name__ == "__main__":
    main()
