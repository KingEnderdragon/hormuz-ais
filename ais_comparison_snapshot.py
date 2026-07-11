"""
Vessel-level comparison between our own AISstream collector (collector.py ->
hormuz_ais.db) and straits.live's live-presence figures (straits_poller.py ->
straits_live_timeseries.csv), per GitHub issue #6 section 2 ("Live AIS
comparison").

Common geographic boundary: straits.live's own documented "AIS-dark filter
zone", 24-28 N, 55-58 E (see straits.live/methodology), which collector.py's
BOUNDING_BOXES now matches exactly. Aggregate agreement alone is treated as
insufficient per the issue - this module records per-vessel state (unique
MMSI set, nav-status bucket) at each interval, not just a count, so later
analysis can compare vessel-level sets, not just totals.

IMPORTANT SCOPE NOTE: straits.live/methodology documents its AISstream feed
as "officially BETA with no SLA; multi-hour silent windows are normal," and
explicitly states this live-presence figure "does NOT drive status,
throughput, or the verdict" (those come from IMF PortWatch instead). A
pilot run of a few hours is enough to validate that this comparison's
join/classification logic works and to get a first-cut reading, but is NOT
enough to characterize outage frequency or reconnect behavior - that
still needs the full multi-week run the issue asks for. Every report from
this module says so explicitly rather than implying more than a short
pilot can support.

Usage:
    python ais_comparison_snapshot.py            # run continuously, one snapshot per straits.live poll
    python ais_comparison_snapshot.py --once      # single snapshot, print JSON, exit
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(__file__)
DB_PATH = os.path.join(HERE, "hormuz_ais.db")
STRAITS_CSV = os.path.join(HERE, "straits_live_timeseries.csv")
OUT_CSV = os.path.join(HERE, "ais_comparison_timeseries.csv")

POLL_INTERVAL_SECONDS = 300  # matches straits_poller.py's cadence

# Must match collector.py's BOUNDING_BOXES - the "documented common geographic
# boundary" the issue asks for (straits.live's own "AIS-dark filter zone").
BOUNDING_BOX = {"lat_min": 24.0, "lat_max": 28.0, "lon_min": 55.0, "lon_max": 58.0}

# A position older than this is not counted as "currently observed" - chosen
# as 4x straits.live's own poll cadence to tolerate normal AIS report jitter
# without silently counting stale/dead contacts as concurrent presence.
POSITION_FRESHNESS_MINUTES = 20

# ITU-R M.1371 Navigational Status codes, per issue #6 section 2's ask to
# compare "anchored, stopped, and transiting classifications."
ANCHORED_STATUSES = {1, 5}          # at anchor, moored
STOPPED_STATUSES = {2, 3, 6}        # not under command, restricted maneuverability, aground
# 0 under way (engine), 4 constrained by draught, 7 fishing, 8 sailing,
# 9-14 various underway/HSC/WIG categories - all still "in transit" for this
# comparison's purposes.
TRANSITING_STATUSES = {0, 4, 7, 8, 9, 10, 11, 12, 13, 14}

CSV_FIELDS = [
    "timestamp_utc",
    "straits_as_of",
    "straits_ais_concurrent_in_zone",
    "straits_stranded",
    "our_unique_mmsi_count",
    "our_anchored_count",
    "our_stopped_count",
    "our_transiting_count",
    "our_unknown_status_count",
    "our_stalest_position_age_minutes",
    "our_freshest_position_age_minutes",
    "agreement_delta",  # our_unique_mmsi_count - straits_ais_concurrent_in_zone
    "our_mmsi_list_json",  # for later vessel-level join against straits.live per-vessel data if/when available
    "pilot_window_caveat",
]

PILOT_CAVEAT = (
    "short-pilot reading, not a multi-week outage/reconnect characterization - "
    "see module docstring"
)


def latest_straits_row():
    if not os.path.exists(STRAITS_CSV):
        return None
    with open(STRAITS_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[-1] if rows else None


def classify(nav_status):
    if nav_status in ANCHORED_STATUSES:
        return "anchored"
    if nav_status in STOPPED_STATUSES:
        return "stopped"
    if nav_status in TRANSITING_STATUSES:
        return "transiting"
    return "unknown"


def our_snapshot(as_of_dt):
    conn = sqlite3.connect(DB_PATH)
    cutoff = (as_of_dt - timedelta(minutes=POSITION_FRESHNESS_MINUTES)).isoformat()
    rows = conn.execute(
        """
        SELECT p.mmsi, p.lat, p.lon, p.nav_status, p.ts
        FROM positions p
        INNER JOIN (
            SELECT mmsi, MAX(ts) AS max_ts FROM positions WHERE ts >= ? GROUP BY mmsi
        ) latest ON p.mmsi = latest.mmsi AND p.ts = latest.max_ts
        """,
        (cutoff,),
    ).fetchall()
    conn.close()

    in_box = [
        r for r in rows
        if r[1] is not None and r[2] is not None
        and BOUNDING_BOX["lat_min"] <= r[1] <= BOUNDING_BOX["lat_max"]
        and BOUNDING_BOX["lon_min"] <= r[2] <= BOUNDING_BOX["lon_max"]
    ]

    buckets = {"anchored": 0, "stopped": 0, "transiting": 0, "unknown": 0}
    ages_minutes = []
    mmsi_list = []
    for mmsi, lat, lon, nav_status, ts in in_box:
        buckets[classify(nav_status)] += 1
        mmsi_list.append(mmsi)
        pos_dt = datetime.fromisoformat(ts)
        ages_minutes.append((as_of_dt - pos_dt).total_seconds() / 60.0)

    return {
        "unique_mmsi_count": len(in_box),
        "anchored_count": buckets["anchored"],
        "stopped_count": buckets["stopped"],
        "transiting_count": buckets["transiting"],
        "unknown_status_count": buckets["unknown"],
        "stalest_position_age_minutes": round(max(ages_minutes), 2) if ages_minutes else None,
        "freshest_position_age_minutes": round(min(ages_minutes), 2) if ages_minutes else None,
        "mmsi_list": sorted(mmsi_list),
    }


def ensure_csv():
    if not os.path.exists(OUT_CSV):
        with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()
        return
    with open(OUT_CSV, newline="", encoding="utf-8") as f:
        existing_header = next(csv.reader(f), [])
    if existing_header != CSV_FIELDS:
        raise RuntimeError(
            f"{OUT_CSV} header does not match current CSV_FIELDS - refusing to "
            f"append (this exact failure mode previously corrupted "
            f"straits_live_timeseries.csv by silent column misalignment).\n"
            f"existing header: {existing_header}\ncurrent CSV_FIELDS: {CSV_FIELDS}"
        )


def take_snapshot():
    now = datetime.now(timezone.utc)
    straits_row = latest_straits_row()
    ours = our_snapshot(now)

    straits_ais = int(straits_row["ais_concurrent_in_zone"]) if straits_row and straits_row.get("ais_concurrent_in_zone") else None
    straits_stranded = int(straits_row["stranded"]) if straits_row and straits_row.get("stranded") else None

    row = {
        "timestamp_utc": now.isoformat(),
        "straits_as_of": straits_row.get("straits_as_of") if straits_row else None,
        "straits_ais_concurrent_in_zone": straits_ais,
        "straits_stranded": straits_stranded,
        "our_unique_mmsi_count": ours["unique_mmsi_count"],
        "our_anchored_count": ours["anchored_count"],
        "our_stopped_count": ours["stopped_count"],
        "our_transiting_count": ours["transiting_count"],
        "our_unknown_status_count": ours["unknown_status_count"],
        "our_stalest_position_age_minutes": ours["stalest_position_age_minutes"],
        "our_freshest_position_age_minutes": ours["freshest_position_age_minutes"],
        "agreement_delta": (ours["unique_mmsi_count"] - straits_ais) if straits_ais is not None else None,
        "our_mmsi_list_json": json.dumps(ours["mmsi_list"]),
        "pilot_window_caveat": PILOT_CAVEAT,
    }
    ensure_csv()
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)
    return row


def run():
    print(f"Snapshotting AIS comparison every {POLL_INTERVAL_SECONDS}s -> {OUT_CSV}")
    print(f"Common box: {BOUNDING_BOX} | freshness window: {POSITION_FRESHNESS_MINUTES} min")
    print(f"NOTE: {PILOT_CAVEAT}")
    while True:
        try:
            row = take_snapshot()
            print(f"{row['timestamp_utc']} ours={row['our_unique_mmsi_count']} "
                  f"(anchored={row['our_anchored_count']} stopped={row['our_stopped_count']} "
                  f"transiting={row['our_transiting_count']} unknown={row['our_unknown_status_count']}) "
                  f"straits={row['straits_ais_concurrent_in_zone']} delta={row['agreement_delta']} "
                  f"freshest={row['our_freshest_position_age_minutes']}min stalest={row['our_stalest_position_age_minutes']}min")
        except Exception as e:
            print(f"Snapshot failed: {e}")
        time.sleep(POLL_INTERVAL_SECONDS)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.once:
        row = take_snapshot()
        print(json.dumps(row, indent=2))
        sys.exit(0)
    try:
        run()
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
