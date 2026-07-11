import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone

import websockets
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.environ["AISSTREAM_API_KEY"]
WS_URL = "wss://stream.aisstream.io/v0/stream"
DB_PATH = os.path.join(os.path.dirname(__file__), "hormuz_ais.db")

# Matches straits.live's own documented "AIS-dark filter zone" (24-28N, 55-58E,
# per straits.live/methodology) rather than an ad-hoc box, so our collector and
# straits.live's live-presence feed are comparable over the same geography -
# see GitHub issue #6 section 2 ("Live AIS comparison").
BOUNDING_BOXES = [[[24.0, 55.0], [28.0, 58.0]]]

TANKER_TYPE_MIN, TANKER_TYPE_MAX = 80, 89


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ships (
            mmsi INTEGER PRIMARY KEY,
            name TEXT,
            ship_type INTEGER,
            imo INTEGER,
            callsign TEXT,
            destination TEXT,
            max_draught REAL,
            last_updated TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mmsi INTEGER,
            lat REAL,
            lon REAL,
            sog REAL,
            cog REAL,
            heading INTEGER,
            nav_status INTEGER,
            ts TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_mmsi_ts ON positions (mmsi, ts)")
    conn.commit()
    return conn


def upsert_ship(conn, mmsi, data):
    conn.execute("""
        INSERT INTO ships (mmsi, name, ship_type, imo, callsign, destination, max_draught, last_updated)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mmsi) DO UPDATE SET
            name=excluded.name,
            ship_type=excluded.ship_type,
            imo=excluded.imo,
            callsign=excluded.callsign,
            destination=excluded.destination,
            max_draught=excluded.max_draught,
            last_updated=excluded.last_updated
    """, (
        mmsi,
        data.get("Name", "").strip() or None,
        data.get("Type"),
        data.get("ImoNumber") or None,
        data.get("CallSign", "").strip() or None,
        data.get("Destination", "").strip() or None,
        data.get("MaximumStaticDraught"),
        datetime.now(timezone.utc).isoformat(),
    ))


def insert_position(conn, mmsi, data):
    conn.execute("""
        INSERT INTO positions (mmsi, lat, lon, sog, cog, heading, nav_status, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        mmsi,
        data.get("Latitude"),
        data.get("Longitude"),
        data.get("Sog"),
        data.get("Cog"),
        data.get("TrueHeading"),
        data.get("NavigationalStatus"),
        datetime.now(timezone.utc).isoformat(),
    ))


async def run():
    conn = init_db()
    pos_count = 0
    static_count = 0

    while True:
        try:
            async with websockets.connect(WS_URL) as ws:
                subscribe_msg = {
                    "APIKey": API_KEY,
                    "BoundingBoxes": BOUNDING_BOXES,
                    "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
                }
                await ws.send(json.dumps(subscribe_msg))
                print(f"Subscribed to bounding box {BOUNDING_BOXES}")

                async for raw in ws:
                    msg = json.loads(raw)
                    mtype = msg.get("MessageType")
                    mmsi = msg.get("MetaData", {}).get("MMSI")

                    if mtype == "PositionReport":
                        data = msg["Message"]["PositionReport"]
                        insert_position(conn, mmsi, data)
                        pos_count += 1
                    elif mtype == "ShipStaticData":
                        data = msg["Message"]["ShipStaticData"]
                        upsert_ship(conn, mmsi, data)
                        static_count += 1

                    print(f"[{mtype}] mmsi={mmsi} name={msg.get('MetaData', {}).get('ShipName')}")

                    if (pos_count + static_count) % 20 == 0 and (pos_count + static_count) > 0:
                        conn.commit()
                        print(f"-- positions={pos_count} static={static_count} --")

        except (websockets.ConnectionClosed, OSError) as e:
            print(f"Connection lost ({e}), reconnecting in 5s...")
            conn.commit()
            await asyncio.sleep(5)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("Stopped.")
