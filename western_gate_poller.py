import csv
import os
import time
import urllib.request
from datetime import datetime, timezone

STATION_ID = 2528
STATION_NAME = "Abu Dhabi"
GATE = "western_persian_gulf_gate"  # single-station proxy, NOT a comprehensive line-crossing count

CSV_PATH = os.path.join(os.path.dirname(__file__), "western_gate_timeseries.csv")
POLL_INTERVAL_SECONDS = 120  # site's own dashboard refreshes every 60s; stay at/above that

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": f"https://www.aishub.net/stations/{STATION_ID}",
}

CSV_FIELDS = [
    "timestamp_utc", "gate", "station_id", "station_name",
    "ships_all", "ships_unique", "uptime_pct",
    "cargo", "tankers", "high_speed", "other_auxiliary", "unknown",
]


def fetch_realtime():
    url = f"https://www.aishub.net/station/{STATION_ID}/realtime.json"
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        import json
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def parse_row(data):
    labels = data["shipType"]["labels"]
    counts = data["shipType"]["datasets"][0]["data"] if data["shipType"]["datasets"] else []
    by_label = dict(zip(labels, counts))

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "gate": GATE,
        "station_id": STATION_ID,
        "station_name": STATION_NAME,
        "ships_all": data["ships"]["all"],
        "ships_unique": data["ships"]["unique"],
        "uptime_pct": data["uptime"],
        "cargo": by_label.get("Cargo", 0),
        "tankers": by_label.get("Tankers", 0),
        "high_speed": by_label.get("High speed", 0),
        "other_auxiliary": by_label.get("Other/Auxiliary", 0),
        "unknown": by_label.get("Unknown", 0),
    }


def ensure_csv():
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()


def run():
    ensure_csv()
    print(f"Polling station {STATION_ID} ({STATION_NAME}) every {POLL_INTERVAL_SECONDS}s -> {CSV_PATH}")
    while True:
        try:
            data = fetch_realtime()
            row = parse_row(data)
            with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)
            print(f"{row['timestamp_utc']} ships_all={row['ships_all']} "
                  f"tankers={row['tankers']} uptime={row['uptime_pct']}%")
        except Exception as e:
            print(f"Poll failed: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("Stopped.")
