"""
Reproducible pipeline for the GFW/UANI tanker cross-reference in
gfw_uani_matches.json.

What this does, and what it explicitly does NOT claim:
- Pulls Global Fishing Watch's `public-global-presence` dataset via the
  4wings/report API for a bounding box covering the Hormuz/Gulf of
  Oman/western-gate region. This is NOT raw per-message AIS - GFW's own
  docs describe it as "one position per hour per vessel", gridded to
  spatial-resolution=LOW (10th-degree cells). It's hourly-sampled,
  derived, gridded vessel presence, not a continuous position stream.
- Extracts every IMO number mentioned in the scraped UANI Iran Tanker
  Tracker corpus (hormuz_ais.db, uani_posts table).
- Cross-references by IMO (a stable identifier independent of GFW's
  sampling/gridding resolution, unlike position or MMSI which can
  change).
- Writes gfw_uani_matches.json with full query provenance so the
  result can be checked or re-run, not just trusted.

Requires: GFW_API_KEY in .env, and hormuz_ais.db populated by
uani_monitor.py (both are local/gitignored - this script documents and
reproduces the pipeline, it doesn't commit the raw GFW response, which
is ~100MB and regenerable on demand).
"""
import json
import os
import re
import sqlite3
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

GFW_TOKEN = os.environ["GFW_API_KEY"]
GFW_BASE_URL = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"
GFW_DATASET = "public-global-presence:latest"
GFW_SPATIAL_RESOLUTION = "LOW"  # 10th-degree grid cells (~11km) - not raw positions
GFW_TEMPORAL_RESOLUTION = "DAILY"
GFW_DATE_RANGE = "2026-06-10,2026-07-09"  # 30-day window used for the committed result

# Hormuz strait / Gulf of Oman / western Persian Gulf gate box
BBOX_MIN_LON, BBOX_MIN_LAT = 54.0, 22.0
BBOX_MAX_LON, BBOX_MAX_LAT = 62.0, 28.0

HORMUZ_AIS_DB = os.path.join(os.path.dirname(__file__), "hormuz_ais.db")
OUT_PATH = os.path.join(os.path.dirname(__file__), "gfw_uani_matches.json")

HEADERS = {
    "Authorization": f"Bearer {GFW_TOKEN}",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}


def fetch_gfw_presence():
    geojson = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [BBOX_MIN_LON, BBOX_MIN_LAT], [BBOX_MAX_LON, BBOX_MIN_LAT],
                [BBOX_MAX_LON, BBOX_MAX_LAT], [BBOX_MIN_LON, BBOX_MAX_LAT],
                [BBOX_MIN_LON, BBOX_MIN_LAT],
            ]],
        },
    }
    url = (
        f"{GFW_BASE_URL}?spatial-resolution={GFW_SPATIAL_RESOLUTION}"
        f"&temporal-resolution={GFW_TEMPORAL_RESOLUTION}"
        f"&datasets[0]={GFW_DATASET}&date-range={GFW_DATE_RANGE}&format=JSON"
    )
    body = json.dumps({"geojson": geojson}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST", headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read().decode("utf-8", errors="ignore"))
    return resp["entries"][0][f"{GFW_DATASET.split(':')[0]}:v4.0"]


def dedupe_by_imo(entries):
    by_imo = defaultdict(lambda: {"hours": 0, "flag": None, "shipName": None,
                                   "vesselType": None, "mmsi": None, "dates": set()})
    for e in entries:
        imo = e.get("imo")
        if not imo:
            continue
        d = by_imo[imo]
        d["hours"] += e.get("hours", 0) or 0
        d["flag"] = e.get("flag")
        d["shipName"] = e.get("shipName")
        d["vesselType"] = e.get("vesselType")
        d["mmsi"] = e.get("mmsi")
        d["dates"].add(e.get("date"))
    return by_imo


def extract_uani_imos():
    conn = sqlite3.connect(HORMUZ_AIS_DB)
    rows = conn.execute("SELECT title, body_excerpt FROM uani_posts").fetchall()
    imos = {}
    for title, body in rows:
        text = (title or "") + " " + (body or "")
        for m in re.finditer(r"IMO:?\s*(\d{7})", text):
            imos[m.group(1)] = title
    return imos


def main():
    print("Fetching GFW public-global-presence...")
    entries = fetch_gfw_presence()
    by_imo = dedupe_by_imo(entries)
    print(f"  {len(entries)} records, {len(by_imo)} distinct vessels with IMO")

    print("Extracting UANI-mentioned IMOs from hormuz_ais.db...")
    uani_imos = extract_uani_imos()
    print(f"  {len(uani_imos)} distinct IMOs mentioned across UANI corpus")

    matches = []
    for imo in sorted(set(uani_imos) & set(by_imo)):
        d = by_imo[imo]
        matches.append({
            "imo": imo,
            "currently_broadcasting_as": d["shipName"],
            "flag": d["flag"],
            "gfw_vessel_type": d["vesselType"],
            "presence_hours_in_window": round(d["hours"], 1),
            "presence_days_in_window": len(d["dates"]),
            "mmsi": d["mmsi"],
            "uani_source_article": uani_imos[imo],
        })

    summary = {
        "method": (
            "Cross-reference by IMO of Global Fishing Watch's public-global-presence "
            "dataset (hourly-sampled, gridded AIS-derived vessel presence - NOT raw "
            "continuous per-message positions; GFW's own docs describe it as one "
            "position per hour per vessel) against IMO numbers mentioned in the "
            "scraped UANI Iran Tanker Tracker corpus. IMO is used as the join key "
            "because it's a stable identifier independent of GFW's sampling/gridding "
            "resolution, unlike raw lat/lon or MMSI (which can be reassigned/spoofed)."
        ),
        "gfw_query": {
            "dataset": GFW_DATASET,
            "spatial_resolution": GFW_SPATIAL_RESOLUTION,
            "temporal_resolution": GFW_TEMPORAL_RESOLUTION,
            "date_range": GFW_DATE_RANGE,
            "bbox_lon_lat": [BBOX_MIN_LON, BBOX_MIN_LAT, BBOX_MAX_LON, BBOX_MAX_LAT],
        },
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "gfw_region_total_records": len(entries),
        "gfw_distinct_vessels_with_imo": len(by_imo),
        "uani_imos_checked": [
            {"imo": imo, "source_article": title} for imo, title in sorted(uani_imos.items())
        ],
        "matches": matches,
    }

    with open(OUT_PATH, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Wrote {OUT_PATH}: {len(matches)} match(es) out of {len(uani_imos)} UANI IMOs checked")


if __name__ == "__main__":
    main()
