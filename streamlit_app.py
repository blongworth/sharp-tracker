from __future__ import annotations

from datetime import timedelta

import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from sharp_tracker.db import db_label, fetch_positions_df

st.set_page_config(page_title="SHARP Tracker", layout="wide")
st.title("SHARP Tracker")


def load_positions() -> pd.DataFrame:
    try:
        return fetch_positions_df()
    except Exception as exc:
        st.error(f"Could not read database {db_label()}: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=5)
def cached_positions() -> pd.DataFrame:
    return load_positions()


with st.sidebar:
    st.header("Data")
    st.caption(f"Database: `{db_label()}`")
    auto_refresh = st.toggle("Auto-refresh every 10 seconds", value=True)
    if st.button("Refresh now"):
        st.cache_data.clear()

if auto_refresh:
    st.html("<script>setTimeout(() => window.location.reload(), 10000)</script>")

df = cached_positions()
if df.empty:
    st.info("No positions logged yet. Start the collector with `python -m sharp_tracker.collector`.")
    st.stop()

# Prefer device fix time when present; otherwise use local receive time.
df["display_time"] = df["fix_time"].fillna(df["received_at"])
df = df.dropna(subset=["display_time", "latitude", "longitude"])

min_time = df["display_time"].min().to_pydatetime()
max_time = df["display_time"].max().to_pydatetime()

with st.sidebar:
    st.header("Filters")
    if min_time == max_time:
        start_time, end_time = min_time, max_time
        st.caption(f"Only one timestamp: {min_time.isoformat()}")
    else:
        default_start = max(min_time, max_time - timedelta(hours=6))
        start_time, end_time = st.slider(
            "Timestamp range",
            min_value=min_time,
            max_value=max_time,
            value=(default_start, max_time),
            format="YYYY-MM-DD HH:mm:ss",
        )
    show_points = st.checkbox("Show points", value=True)
    show_track = st.checkbox("Show track line", value=True)

filtered = df[(df["display_time"] >= start_time) & (df["display_time"] <= end_time)].copy()

metric_cols = st.columns(4)
metric_cols[0].metric("Total fixes", len(df))
metric_cols[1].metric("Displayed fixes", len(filtered))
metric_cols[2].metric("First displayed", filtered["display_time"].min().strftime("%H:%M:%S") if not filtered.empty else "—")
metric_cols[3].metric("Last displayed", filtered["display_time"].max().strftime("%H:%M:%S") if not filtered.empty else "—")

if filtered.empty:
    st.warning("No positions match the selected timestamp range.")
    st.stop()

center = [filtered["latitude"].mean(), filtered["longitude"].mean()]
map_obj = folium.Map(location=center, zoom_start=12, tiles=None)
folium.TileLayer(
    tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}",
    attr="Esri, Garmin, GEBCO, NOAA NGDC, and other contributors",
    name="Esri Ocean Basemap",
    overlay=False,
    control=True,
).add_to(map_obj)
folium.TileLayer("OpenStreetMap", name="OpenStreetMap", control=True).add_to(map_obj)

coords = filtered[["latitude", "longitude"]].values.tolist()
if show_track and len(coords) > 1:
    folium.PolyLine(coords, color="#ff4b4b", weight=3, opacity=0.85, tooltip="Track").add_to(map_obj)

if show_points:
    for row in filtered.itertuples(index=False):
        label = row.display_time.strftime("%Y-%m-%d %H:%M:%S UTC")
        popup = folium.Popup(
            f"<b>{label}</b><br>Lat: {row.latitude:.6f}<br>Lon: {row.longitude:.6f}<br>"
            f"Speed: {row.speed_knots if pd.notna(row.speed_knots) else '—'} kn<br>"
            f"Course: {row.course_deg if pd.notna(row.course_deg) else '—'}°",
            max_width=300,
        )
        folium.CircleMarker(
            [row.latitude, row.longitude],
            radius=4,
            color="#1f77b4",
            fill=True,
            fill_opacity=0.8,
            popup=popup,
            tooltip=label,
        ).add_to(map_obj)

folium.Marker(
    coords[-1],
    tooltip="Latest displayed position",
    icon=folium.Icon(color="red", icon="location-dot", prefix="fa"),
).add_to(map_obj)
folium.LayerControl().add_to(map_obj)

st_folium(map_obj, width=None, height=650)

with st.expander("Recent displayed fixes"):
    st.dataframe(filtered.tail(100).sort_values("display_time", ascending=False), use_container_width=True)
