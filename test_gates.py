import asyncio
import json
import os
import time
from datetime import datetime, timezone

import websockets
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.environ["AISSTREAM_API_KEY"]
WS_URL = "wss://stream.aisstream.io/v0/stream"

# Gulf of Oman gate: approach from Arabian Sea, east/southeast of Muscat,
# where Gulf-bound traffic funnels in before Hormuz.
GOO_GATE = [[23.5, 58.0], [25.5, 60.0]]

# Western gate: where the strait opens into the Persian Gulf proper
# (UAE/Qeshm/Bandar Abbas approach), northwest end.
PG_GATE = [[25.5, 54.0], [27.5, 56.5]]

RUN_SECONDS = 300


async def run():
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "APIKey": API_KEY,
            "BoundingBoxes": [GOO_GATE, PG_GATE],
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
        }))
        print(f"Subscribed to both gate boxes: GOO={GOO_GATE} PG={PG_GATE}")
        print(f"Listening for {RUN_SECONDS}s...")

        start = time.monotonic()
        count = 0
        try:
            while time.monotonic() - start < RUN_SECONDS:
                remaining = RUN_SECONDS - (time.monotonic() - start)
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                count += 1
                msg = json.loads(raw)
                mtype = msg.get("MessageType")
                meta = msg.get("Metadata", {})
                print(f"[{datetime.now(timezone.utc).isoformat()}] {mtype} "
                      f"mmsi={meta.get('MMSI')} name={meta.get('ShipName')} "
                      f"lat={meta.get('latitude')} lon={meta.get('longitude')}")
        except asyncio.TimeoutError:
            pass

        print(f"\nDONE. Total messages in {RUN_SECONDS}s: {count}")


asyncio.run(run())
