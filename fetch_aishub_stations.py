"""
Collection script for aishub_stations.json.

Fetches AISHub's public, unauthenticated station-list export. This is
AISHub's own network of contributor-hosted receivers - a separate
network from aisstream.io, not a measurement of aisstream's
infrastructure. It's used in overlay_map.py purely as a general
reference for what a real-world AIS receiver network's geographic
distribution looks like (station density vs. ship traffic density),
not as a claim about where aisstream specifically has coverage.

Source: https://www.aishub.net/stations/export-json (same JSON the
station list page itself renders from - no API key or AISHub
membership required to read it).
"""
import json
import urllib.request

EXPORT_URL = "https://www.aishub.net/stations/export-json"
OUT_PATH = "aishub_stations.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}


def fetch():
    req = urllib.request.Request(EXPORT_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def main():
    stations = fetch()
    valid = [
        s for s in stations
        if s.get("latitude") and s.get("longitude")
        and not (float(s["latitude"]) == 0.0 and float(s["longitude"]) == 0.0)
    ]
    print(f"total stations: {len(stations)}, with valid coords: {len(valid)}")

    with open(OUT_PATH, "w") as f:
        json.dump(valid, f)
    print(f"saved {OUT_PATH}")


if __name__ == "__main__":
    main()
