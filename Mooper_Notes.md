# Mooper Notes

1. [Worldwide Snapshot Collection] -- Connected to the AISstream.io websocket feed with a global bounding box (`[[-90,-180],[90,180]]`) and no message-type filter, then collected everything the feed sent for a fixed 30-second window using a one-off script (`snapshot.py`). Raw JSON messages were counted by type and by unique MMSI as they arrived, and every `PositionReport`'s lon/lat pair was appended to a list for later plotting.

2. [MetaData Field Bug] -- Initial runs showed 0 unique vessels because the code read `msg["Metadata"]`, but AISstream actually nests vessel identity under `MetaData` (capital D) with keys like `MMSI`, `ShipName`, `latitude`/`longitude`. Fixed by correcting the key name in `snapshot.py`; the same typo likely exists in `collector.py` and should be checked before running it against the Hormuz bounding box.

3. [Snapshot Results] -- The 30-second worldwide capture returned 4,738 messages across 4,277 unique vessels, including 2,244 `PositionReport` and 907 `StandardClassBPositionReport` entries with coordinates, plus static data, navigation aid, base station, and SAR aircraft messages. Coordinates were dumped to `snapshot_positions.json` and plotted with matplotlib (`snapshot_map.png`), where vessel density alone traces recognizable coastlines and shipping lanes.
