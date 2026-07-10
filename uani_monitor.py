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


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS uani_posts (
            url TEXT PRIMARY KEY,
            title TEXT,
            category TEXT,
            severity TEXT,
            body_excerpt TEXT,
            first_seen TEXT
        )
    """)
    conn.commit()
    return conn


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
    conn.commit()

    tag = "[!! HIGH !!]" if severity == "HIGH" else "[normal]"
    print(f"{tag} NEW {category}: {title}\n    {url}")
    if severity == "HIGH":
        print(f"    excerpt: {excerpt[:300]}")


def run():
    conn = init_db()
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
    try:
        run()
    except KeyboardInterrupt:
        print("Stopped.")
