import re
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone

from bs4 import BeautifulSoup

HUB_URL = "https://www.unitedagainstnucleariran.com/tanker-tracker"
BASE = "https://www.unitedagainstnucleariran.com"
DB_PATH = "hormuz_ais.db"
POLL_INTERVAL_SECONDS = 900  # 15 min

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}

HIGH_SEVERITY_TERMS = [
    "seizure", "seized", "seize", "attack", "strike", "struck", "collision",
    "explosion", "blocked", "blockade", "closure", "closed", "naval",
    "military", "mine", "missile", "sunk", "sinking", "evacuat", "boarding",
    "boarded", "detained", "detention", "gunfire", "warship",
]


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", errors="ignore")


# Bump this when process_post()'s IMO-extraction/validation logic changes.
# backfill_imo_mentions() re-processes any post whose stored
# imo_extraction_version is older than this, so a logic change (e.g. adding
# check-digit validation) doesn't require the user to manually delete the
# database and re-scrape from scratch.
IMO_EXTRACTION_VERSION = 2


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS uani_posts (
            url TEXT PRIMARY KEY,
            title TEXT,
            category TEXT,
            severity TEXT,
            body_excerpt TEXT,
            first_seen TEXT,
            imo_extraction_version INTEGER DEFAULT 0
        )
    """)
    # Existing DBs from before this column existed won't have it - add it if
    # missing rather than requiring a fresh database.
    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(uani_posts)")}
    if "imo_extraction_version" not in existing_cols:
        conn.execute("ALTER TABLE uani_posts ADD COLUMN imo_extraction_version INTEGER DEFAULT 0")

    # Extracted from full body_text at scrape time, before it's truncated to
    # body_excerpt - body_excerpt is capped at 1500 chars for display/storage
    # size, which silently misses IMOs mentioned later in longer articles.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS uani_imo_mentions (
            imo TEXT,
            url TEXT,
            title TEXT,
            first_seen TEXT,
            PRIMARY KEY (imo, url)
        )
    """)
    # 7-digit strings that looked like IMOs (matched IMO_RE) but failed the
    # check-digit validation - kept as a record rather than silently dropped
    # or, worse, silently counted as valid checked IMOs.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS uani_imo_extraction_anomalies (
            candidate TEXT,
            url TEXT,
            title TEXT,
            reason TEXT,
            first_seen TEXT,
            PRIMARY KEY (candidate, url)
        )
    """)
    conn.commit()
    return conn


IMO_RE = re.compile(r"IMO:?\s*(\d{7})")


def is_valid_imo(imo):
    """IMO number check-digit validation: the 7th digit must equal
    (d1*7 + d2*6 + d3*5 + d4*4 + d5*3 + d6*2) mod 10."""
    digits = [int(c) for c in imo]
    total = sum(d * w for d, w in zip(digits[:6], [7, 6, 5, 4, 3, 2]))
    return total % 10 == digits[6]


def classify_severity(text):
    lowered = text.lower()
    for term in HIGH_SEVERITY_TERMS:
        if re.search(r"\b" + re.escape(term), lowered):
            return "HIGH"
    return "NORMAL"


def discover_links():
    html = fetch(HUB_URL)
    links = re.findall(r'href="(/blog/[^"]+|/analysis/[^"]+)"', html)
    seen = []
    for l in links:
        if l not in seen:
            seen.append(l)
    return seen


def extract_and_store_imos(conn, url, title, body_text):
    """Splits IMO_RE candidates into valid mentions vs. check-digit
    anomalies, and marks the post as processed at IMO_EXTRACTION_VERSION."""
    now = datetime.now(timezone.utc).isoformat()
    valid, invalid = 0, 0
    for candidate in set(IMO_RE.findall(title + " " + body_text)):
        if is_valid_imo(candidate):
            conn.execute("""
                INSERT OR IGNORE INTO uani_imo_mentions (imo, url, title, first_seen)
                VALUES (?, ?, ?, ?)
            """, (candidate, url, title, now))
            valid += 1
        else:
            conn.execute("""
                INSERT OR IGNORE INTO uani_imo_extraction_anomalies
                (candidate, url, title, reason, first_seen)
                VALUES (?, ?, ?, ?, ?)
            """, (candidate, url, title, "failed_check_digit", now))
            invalid += 1
    conn.execute(
        "UPDATE uani_posts SET imo_extraction_version = ? WHERE url = ?",
        (IMO_EXTRACTION_VERSION, url),
    )
    return valid, invalid


def process_post(conn, path):
    url = BASE + path
    html = fetch(url)
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    title = h1.get_text(strip=True) if h1 else path

    body_el = soup.find(class_="field--name-body")
    body_text = body_el.get_text(" ", strip=True) if body_el else ""
    excerpt = body_text[:1500]

    category = "analysis" if path.startswith("/analysis/") else "blog"
    severity = classify_severity(title + " " + body_text)

    conn.execute("""
        INSERT OR IGNORE INTO uani_posts (url, title, category, severity, body_excerpt, first_seen)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (url, title, category, severity, excerpt, datetime.now(timezone.utc).isoformat()))

    # IMOs from full body_text (not the truncated excerpt), so mentions past
    # the 1500-char cutoff aren't silently missed.
    extract_and_store_imos(conn, url, title, body_text)
    conn.commit()

    tag = "[!! HIGH !!]" if severity == "HIGH" else "[normal]"
    print(f"{tag} NEW {category}: {title}\n    {url}")
    if severity == "HIGH":
        print(f"    excerpt: {excerpt[:300]}")


def backfill_imo_mentions(conn):
    """Re-fetches and re-extracts IMOs for any post whose
    imo_extraction_version predates IMO_EXTRACTION_VERSION - covers both
    posts scraped before uani_imo_mentions existed at all, and posts
    processed under an older (e.g. unvalidated) extraction version.
    IMO extraction needs the full article body_text, which isn't persisted
    (only the truncated body_excerpt is), so this means re-fetching each
    page rather than reprocessing stored data."""
    rows = conn.execute(
        "SELECT url, title FROM uani_posts WHERE imo_extraction_version < ?",
        (IMO_EXTRACTION_VERSION,),
    ).fetchall()
    if not rows:
        return
    print(f"Backfilling IMO extraction for {len(rows)} post(s) at version {IMO_EXTRACTION_VERSION}...")
    total_valid, total_invalid = 0, 0
    for url, title in rows:
        try:
            html = fetch(url)
            soup = BeautifulSoup(html, "html.parser")
            body_el = soup.find(class_="field--name-body")
            body_text = body_el.get_text(" ", strip=True) if body_el else ""
            valid, invalid = extract_and_store_imos(conn, url, title, body_text)
            total_valid += valid
            total_invalid += invalid
            conn.commit()
        except Exception as e:
            print(f"  Backfill failed for {url}: {e}")
    print(f"Backfill done: {total_valid} valid IMO mentions, {total_invalid} check-digit anomalies.")


def run():
    conn = init_db()
    backfill_imo_mentions(conn)
    known = {row[0] for row in conn.execute("SELECT url FROM uani_posts")}
    print(f"Loaded {len(known)} known posts. Polling every {POLL_INTERVAL_SECONDS}s.")

    while True:
        try:
            paths = discover_links()
            new_paths = [p for p in paths if (BASE + p) not in known]
            if new_paths:
                print(f"{datetime.now(timezone.utc).isoformat()} — {len(new_paths)} new post(s)")
            for p in reversed(new_paths):  # oldest-first
                try:
                    process_post(conn, p)
                    known.add(BASE + p)
                except Exception as e:
                    print(f"Failed to process {p}: {e}")
        except Exception as e:
            print(f"Poll cycle failed: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    import sys
    if "--backfill-only" in sys.argv:
        backfill_imo_mentions(init_db())
    else:
        try:
            run()
        except KeyboardInterrupt:
            print("Stopped.")
