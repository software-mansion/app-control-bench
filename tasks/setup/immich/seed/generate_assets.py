#!/usr/bin/env python3
"""
Generate a deterministic, license-clean benchmark photo/video library for Immich.

- ~60 JPEGs + 4 MP4s, drawn with Pillow (synthetic — no network, reproducible).
- EXIF stamped with exiftool: DateTimeOriginal (spread 2019..now + an "on this day"
  memories cluster on today's month/day across the last 5 years), GPS on a subset
  (for the Places/map view), and an ImageDescription encoding the scene ("beach ...").
- Videos stamped with a QuickTime creation_time via ffmpeg.
- Emits assets.manifest.json describing every asset (date, gps, category, album,
  favorite, is_memory) which seed.py then uploads + organises via the Immich API.

Run with the venv python (Pillow):  seed/.venv/bin/python seed/generate_assets.py
Dates are re-derived from "now" on every run so memories stay fresh for the run date.
"""
import json
import os
import random
import subprocess
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "assets")
MANIFEST = os.path.join(HERE, "assets.manifest.json")

random.seed(1337)  # deterministic layout/date assignment
NOW = datetime.now()

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    sys.exit("Pillow missing — run:  seed/.venv/bin/pip install Pillow")

# ── scene catalogue ───────────────────────────────────────────────────────────
# color = base gradient; gps = list of (name, lat, lon) to rotate through (or None)
CITY_COORDS = {
    "New York":  (40.7128, -74.0060),
    "Paris":     (48.8566, 2.3522),
    "Tokyo":     (35.6762, 139.6503),
    "London":    (51.5074, -0.1278),
}
BEACH_COORDS = {
    "Barcelona": (41.3784, 2.1925),
    "Miami":     (25.7907, -80.1300),
    "Sydney":    (-33.8908, 151.2743),
}
CATS = {
    #  name        count  top-color        bot-color       gps-pool
    "beach":    (12, (34, 148, 199), (250, 232, 170), BEACH_COORDS),
    "mountain": (10, (70, 92, 120),  (210, 220, 228), None),
    "city":     (12, (90, 96, 110),  (232, 214, 176), CITY_COORDS),
    "people":   (8,  (176, 128, 120), (250, 240, 235), None),
    "nature":   (14, (44, 128, 82),  (226, 238, 190), None),
}
SUMMER_ALBUM = "Summer Vacation"   # beach + a few nature (immich-03)
CITY_ALBUM = "City Trips"          # city (immich-30 / picker variety)

W, H = 1200, 900


def _font(size):
    for p in [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def draw_image(path, cat, top, bot, idx, when, place):
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        t = y / H
        r = int(top[0] * (1 - t) + bot[0] * t)
        g = int(top[1] * (1 - t) + bot[1] * t)
        b = int(top[2] * (1 - t) + bot[2] * t)
        for x in range(W):
            px[x, y] = (r, g, b)
    d = ImageDraw.Draw(img)
    # a few translucent bands so thumbnails differ within a category
    rnd = random.Random(f"{cat}{idx}")
    for _ in range(3):
        y0 = rnd.randint(0, H)
        h = rnd.randint(30, 120)
        shade = rnd.randint(-40, 40)
        band = tuple(max(0, min(255, c + shade)) for c in bot)
        d.rectangle([0, y0, W, y0 + h], fill=band)
    title = f"{cat.capitalize()} {idx:02d}"
    d.text((60, 60), title, font=_font(120), fill=(255, 255, 255))
    sub = when.strftime("%b %d, %Y")
    if place:
        sub = f"{place}  ·  {sub}"
    d.text((62, 210), sub, font=_font(52), fill=(20, 20, 20))
    d.text((62, H - 90), f"app-control-bench sample · {cat}", font=_font(34), fill=(255, 255, 255))
    img.save(path, "JPEG", quality=86)


def exif_stamp(path, when, desc, gps):
    args = [
        "exiftool", "-overwrite_original", "-q",
        f"-DateTimeOriginal={when:%Y:%m:%d %H:%M:%S}",
        f"-CreateDate={when:%Y:%m:%d %H:%M:%S}",
        f"-ModifyDate={when:%Y:%m:%d %H:%M:%S}",
        f"-ImageDescription={desc}",
        f"-XMP-dc:Description={desc}",
        f"-IPTC:Caption-Abstract={desc}",
        "-Make=BenchCam", "-Model=ACB-1",
    ]
    if gps:
        lat, lon = gps
        args += [
            f"-GPSLatitude={abs(lat)}", f"-GPSLatitudeRef={'N' if lat >= 0 else 'S'}",
            f"-GPSLongitude={abs(lon)}", f"-GPSLongitudeRef={'E' if lon >= 0 else 'W'}",
        ]
    args.append(path)
    subprocess.run(args, check=True)


def make_video(path, when, label, src):
    # No drawtext filter on purpose: it needs fontconfig/a fontfile and fails on
    # bare macOS ffmpeg. The lavfi source pattern is enough to identify the clip.
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"{src}=size=1280x720:rate=30:duration=5",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        "-metadata", f"creation_time={when:%Y-%m-%dT%H:%M:%S}Z",
        path,
    ], check=True)


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        os.remove(os.path.join(OUT, f))

    manifest = []

    # spread of "normal" dates across 2019..(now-1 month) so the timeline has many
    # month headers (scroll immich-21) and real history.
    def spread_date(seed_i):
        r = random.Random(f"date{seed_i}")
        start = datetime(2019, 1, 1)
        span_days = (NOW - timedelta(days=40) - start).days
        d = start + timedelta(days=r.randint(0, span_days),
                              hours=r.randint(6, 20), minutes=r.randint(0, 59))
        return d

    counter = 0
    for cat, (n, top, bot, gpspool) in CATS.items():
        coords = list(gpspool.items()) if gpspool else []
        for i in range(1, n + 1):
            counter += 1
            when = spread_date(counter)
            place, gps = (None, None)
            if coords:
                place, gps = coords[(i - 1) % len(coords)]
            fname = f"{cat}-{i:02d}.jpg"
            path = os.path.join(OUT, fname)
            desc = f"{cat} scene {i} at {place}" if place else f"a {cat} scene number {i}"
            draw_image(path, cat, top, bot, i, when, place)
            exif_stamp(path, when, desc, gps)
            album = None
            if cat == "beach":
                album = SUMMER_ALBUM
            elif cat == "city":
                album = CITY_ALBUM
            elif cat == "nature" and i <= 3:
                album = SUMMER_ALBUM  # a few nature shots round out the vacation album
            manifest.append({
                "file": fname, "category": cat, "taken": when.isoformat(),
                "place": place, "gps": gps, "album": album,
                "favorite": False, "is_memory": False, "kind": "image",
            })

    # favorites: first beach, first mountain, first city, a nature (immich-04)
    fav_targets = {"beach-01.jpg", "mountain-01.jpg", "city-01.jpg", "nature-02.jpg", "beach-05.jpg"}
    for m in manifest:
        if m["file"] in fav_targets:
            m["favorite"] = True

    # "On this day" memories cluster: today's month/day across the last 5 years
    # (immich-19). 2 photos/year, tagged is_memory so seed.py can build the memory.
    for yback in range(1, 6):
        year = NOW.year - yback
        try:
            base = NOW.replace(year=year, hour=11, minute=0, second=0, microsecond=0)
        except ValueError:  # Feb 29 guard
            base = NOW.replace(year=year, day=28, hour=11, minute=0, second=0, microsecond=0)
        for j in range(2):
            counter += 1
            when = base + timedelta(hours=j * 3)
            fname = f"memory-{year}-{j+1}.jpg"
            path = os.path.join(OUT, fname)
            place, gps = list(BEACH_COORDS.items())[j % len(BEACH_COORDS)]
            desc = f"on this day memory from {year} — beach at {place}"
            draw_image(path, "beach", CATS["beach"][1], CATS["beach"][2], yback, when, place)
            exif_stamp(path, when, desc, gps)
            manifest.append({
                "file": fname, "category": "beach", "taken": when.isoformat(),
                "place": place, "gps": gps, "album": SUMMER_ALBUM,
                "favorite": False, "is_memory": True, "memory_year": year, "kind": "image",
            })

    # videos (immich-20 playback, immich-28 video filter)
    vids = [
        ("beach-clip.mp4",  "Beach clip",  "testsrc2", NOW - timedelta(days=20)),
        ("city-clip.mp4",   "City clip",   "smptebars", NOW - timedelta(days=120)),
        ("nature-clip.mp4", "Nature clip", "testsrc",  NOW - timedelta(days=400)),
        ("party-clip.mp4",  "Party clip",  "rgbtestsrc", NOW - timedelta(days=800)),
    ]
    for fname, label, src, when in vids:
        path = os.path.join(OUT, fname)
        make_video(path, when, label, src)
        manifest.append({
            "file": fname, "category": "video", "taken": when.isoformat(),
            "place": None, "gps": None,
            "album": SUMMER_ALBUM if fname == "beach-clip.mp4" else None,
            "favorite": False, "is_memory": False, "kind": "video",
        })

    with open(MANIFEST, "w") as fh:
        json.dump({"generated_at": NOW.isoformat(), "count": len(manifest),
                   "assets": manifest}, fh, indent=2)
    imgs = sum(1 for m in manifest if m["kind"] == "image")
    print(f"generated {imgs} images + {len(manifest)-imgs} videos -> {OUT}")
    print(f"manifest -> {MANIFEST}")


if __name__ == "__main__":
    main()
