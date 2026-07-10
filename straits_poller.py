import csv
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

STATUS_URL = "https://straits.live/status"
CSV_PATH = os.path.join(os.path.dirname(__file__), "straits_live_timeseries.csv")
POLL_INTERVAL_SECONDS = 300  # matches straits.live's own fastest refresh cadence (5 min)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
}

CSV_FIELDS = [
    "timestamp_utc", "straits_as_of", "verdict_status", "verdict_short",
    "crisis_pressure", "crisis_band", "escalation_probability", "escalation_band",
    "transits_count", "transits_baseline", "throughput_pct", "transits_as_of_date",
    "daily_tanker_count", "daily_cargo_count", "daily_container_count",
    "ais_concurrent_in_zone", "stranded",
    "ais_gaps_count", "ais_gaps_baseline_7d", "ais_gaps_vessels_tracked",
    "darkening_ratio", "darkening_alert",
    "vessel_risk_high", "vessel_risk_moderate", "vessel_risk_low",
    "brent", "wti",
]

# ais_gaps_count vs its own 7-day baseline: how far above normal is "dark tanker" activity right now
DARKENING_ELEVATED_RATIO = 1.5
DARKENING_HIGH_RATIO = 2.0


def darkening_level(gaps_count, baseline_7d):
    if not baseline_7d:
        return None, "unknown"
    ratio = gaps_count / baseline_7d
    if ratio >= DARKENING_HIGH_RATIO:
        return ratio, "HIGH"
    if ratio >= DARKENING_ELEVATED_RATIO:
        return ratio, "ELEVATED"
    return ratio, "normal"


def fetch_status():
    req = urllib.request.Request(STATUS_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", errors="ignore"))


def parse_row(d):
    hi = d.get("hormuzIndex", {})
    cp = hi.get("crisisPressure", {})
    ep = hi.get("escalationProbability", {})
    tr = d.get("transits", {})
    dt = d.get("dailyTransits", {})
    gaps = d.get("aisGaps", {})
    risk = d.get("vesselRisk", {})
    verdict = d.get("verdict", {})

    ratio, alert = darkening_level(gaps.get("count"), gaps.get("baseline7d"))

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "straits_as_of": d.get("asOf"),
        "verdict_status": verdict.get("status"),
        "verdict_short": verdict.get("short"),
        "crisis_pressure": cp.get("value"),
        "crisis_band": cp.get("band"),
        "escalation_probability": ep.get("value"),
        "escalation_band": ep.get("band"),
        "transits_count": tr.get("count"),
        "transits_baseline": tr.get("baseline"),
        "throughput_pct": tr.get("throughputPct"),
        "transits_as_of_date": tr.get("asOfDate"),
        "daily_tanker_count": dt.get("nTanker"),
        "daily_cargo_count": dt.get("nCargo"),
        "daily_container_count": dt.get("nContainer"),
        "ais_concurrent_in_zone": d.get("aisConcurrentInZone"),
        "stranded": d.get("stranded"),
        "ais_gaps_count": gaps.get("count"),
        "ais_gaps_baseline_7d": gaps.get("baseline7d"),
        "ais_gaps_vessels_tracked": gaps.get("vesselsTracked"),
        "darkening_ratio": round(ratio, 2) if ratio is not None else None,
        "darkening_alert": alert,
        "vessel_risk_high": risk.get("high"),
        "vessel_risk_moderate": risk.get("moderate"),
        "vessel_risk_low": risk.get("low"),
        "brent": d.get("brent"),
        "wti": d.get("wti"),
    }


def ensure_csv():
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()


def run():
    ensure_csv()
    print(f"Polling {STATUS_URL} every {POLL_INTERVAL_SECONDS}s -> {CSV_PATH}")
    while True:
        try:
            data = fetch_status()
            row = parse_row(data)
            with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)
            tag = f"[!! DARKENING {row['darkening_alert']} !!]" if row['darkening_alert'] in ("ELEVATED", "HIGH") else ""
            print(f"{row['timestamp_utc']} status={row['verdict_status']} "
                  f"crisis={row['crisis_pressure']} throughput={row['throughput_pct']}% "
                  f"tankers/day={row['daily_tanker_count']} ais_gaps={row['ais_gaps_count']}"
                  f"/baseline={row['ais_gaps_baseline_7d']} ratio={row['darkening_ratio']} {tag}")
        except Exception as e:
            print(f"Poll failed: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("Stopped.")
