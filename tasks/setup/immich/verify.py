#!/usr/bin/env python3
"""
Health-check the Immich benchmark surface: prove the data the tasks depend on
exists. Read-only. Run after bring-up + seed (and after jobs settle).

Env: IMMICH_URL (default http://localhost:2283),
     IMMICH_EMAIL / IMMICH_PASSWORD (default admin@immich.local / admin1234).
"""
import json
import os
import urllib.request

BASE = os.environ.get("IMMICH_URL", "http://localhost:2283").rstrip("/")
EMAIL = os.environ.get("IMMICH_EMAIL", "admin@immich.local")
PASSWORD = os.environ.get("IMMICH_PASSWORD", "admin1234")


def api(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else {}


def main():
    tok = api("POST", "/api/auth/login", body={"email": EMAIL, "password": PASSWORD})["accessToken"]

    about = api("GET", "/api/server/about", tok)
    print("server version :", about.get("version"))

    st = api("GET", "/api/assets/statistics", tok)
    print("assets         :", f"{st['images']} images, {st['videos']} videos, {st['total']} total")

    albums = api("GET", "/api/albums", tok)
    print("albums         :", ", ".join(f"{a['albumName']}={a['assetCount']}" for a in albums) or "(none)")

    favs = api("POST", "/api/search/metadata", tok, {"isFavorite": True})["assets"]["total"]
    print("favorites      :", favs)

    markers = api("GET", "/api/map/markers", tok)
    print("geotagged      :", len(markers), "map markers (Places)")

    mem = api("GET", "/api/memories", tok)
    print("memories       :", len(mem), "on-this-day")

    beach = api("POST", "/api/search/metadata", tok, {"query": "beach"})["assets"]["total"]
    print("search 'beach' :", beach, "results")

    vids = api("POST", "/api/search/metadata", tok, {"type": "VIDEO"})["assets"]["total"]
    print("videos (search):", vids)

    jobs = api("GET", "/api/jobs", tok)
    for k in ("metadataExtraction", "thumbnailGeneration", "smartSearch", "faceDetection"):
        if k in jobs:
            jc = jobs[k]["jobCounts"]
            print(f"job {k:20s}: waiting={jc['waiting']} active={jc['active']} done? {jc['waiting']==0 and jc['active']==0}")


if __name__ == "__main__":
    main()
