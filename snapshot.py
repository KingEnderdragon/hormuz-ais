import asyncio
import json
import os
import time
from collections import Counter

import websockets
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.environ["AISSTREAM_API_KEY"]
WS_URL = "wss://stream.aisstream.io/v0/stream"

DURATION_S = 30

positions = []
msg_type_counts = Counter()
mmsi_seen = set()
raw_samples = []


async def run():
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "APIKey": API_KEY,
            "BoundingBoxes": [[[-90, -180], [90, 180]]],
        }))
        print(f"subscribed worldwide, collecting for {DURATION_S}s...")
        start = time.time()
        async for raw in ws:
            msg = json.loads(raw)
            mtype = msg.get("MessageType")
            msg_type_counts[mtype] += 1
            mmsi = msg.get("MetaData", {}).get("MMSI")
            if mmsi is not None:
                mmsi_seen.add(mmsi)

            if mtype == "PositionReport":
                data = msg["Message"]["PositionReport"]
                positions.append((data.get("Longitude"), data.get("Latitude")))

            if len(raw_samples) < 3:
                raw_samples.append(raw[:300])

            if time.time() - start > DURATION_S:
                break

asyncio.run(run())

print("\n--- SNAPSHOT SUMMARY ---")
print(f"total messages: {sum(msg_type_counts.values())}")
print(f"unique vessels (MMSI): {len(mmsi_seen)}")
print("message type breakdown:")
for mtype, count in msg_type_counts.most_common():
    print(f"  {mtype}: {count}")
print(f"position reports with coordinates: {len(positions)}")

with open("snapshot_positions.json", "w") as f:
    json.dump(positions, f)
