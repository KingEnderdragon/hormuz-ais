import asyncio
import json
import os

import websockets
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.environ["AISSTREAM_API_KEY"]
WS_URL = "wss://stream.aisstream.io/v0/stream"


async def run():
    async with websockets.connect(WS_URL) as ws:
        await ws.send(json.dumps({
            "APIKey": API_KEY,
            "BoundingBoxes": [[[-90, -180], [90, 180]]],
            "FilterMessageTypes": ["PositionReport"],
        }))
        print("subscribed worldwide, waiting for messages...")
        count = 0
        async for raw in ws:
            count += 1
            if count <= 5:
                print(raw[:200])
            if count >= 30:
                print(f"received {count} messages, worldwide feed confirmed working")
                break


asyncio.run(run())
