import json

import plotly.graph_objects as go

with open("snapshot_positions.json") as f:
    ship_positions = json.load(f)

with open("aishub_stations.json") as f:
    stations = json.load(f)

ship_lons = [p[0] for p in ship_positions]
ship_lats = [p[1] for p in ship_positions]

station_lons = [float(s["longitude"]) for s in stations]
station_lats = [float(s["latitude"]) for s in stations]

fig = go.Figure()

# ships underneath (more numerous)
fig.add_trace(go.Scattergeo(
    lon=ship_lons,
    lat=ship_lats,
    mode="markers",
    marker=dict(size=5, color="royalblue", opacity=0.6, symbol="triangle-up"),
    name=f"Ships reporting (AISstream PositionReport, n={len(ship_positions)})",
))

# stations on top (fixed receiver locations, AISHub network)
fig.add_trace(go.Scattergeo(
    lon=station_lons,
    lat=station_lats,
    mode="markers",
    marker=dict(size=3, color="crimson", opacity=0.85, symbol="circle"),
    name=f"AIS receiving stations (AISHub, n={len(stations)})",
))

fig.update_geos(
    showland=True, landcolor="rgb(235,235,235)",
    showocean=True, oceancolor="rgb(220,235,245)",
    showcountries=True, countrycolor="rgb(200,200,200)",
    showcoastlines=True, coastlinecolor="rgb(120,120,120)",
    projection_type="equirectangular",
)

fig.update_layout(
    title="Ships reporting (blue) vs. fixed AIS receiving stations (red)",
    width=1800, height=950,
    margin=dict(l=10, r=10, t=50, b=10),
    legend=dict(x=0.01, y=0.02, bgcolor="rgba(255,255,255,0.8)"),
)

fig.write_image("snapshot_map_overlay.png", scale=2)
print("wrote snapshot_map_overlay.png")
