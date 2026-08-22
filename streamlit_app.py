from __future__ import annotations

import os
from datetime import timedelta
from io import StringIO
from math import atan2, cos, radians, sin, degrees

import folium
import numpy as np
import pandas as pd
import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from streamlit_folium import st_folium

from sharp_tracker.db import db_label, fetch_positions_df

WAYPOINTS_URL = "https://nes-lter-api.whoi.edu/api/stations/file.csv"


def format_value(value: object, suffix: str = "", decimals: int = 1) -> str:
    if pd.isna(value):
        return "—"
    return f"{float(value):.{decimals}f}{suffix}"


def query_str(name: str, default: str) -> str:
    value = st.query_params.get(name, default)
    return value[0] if isinstance(value, list) else value


def query_float(name: str, default: float) -> float:
    try:
        return float(query_str(name, str(default)))
    except (TypeError, ValueError):
        return default


def query_int(name: str, default: int) -> int:
    try:
        return int(float(query_str(name, str(default))))
    except (TypeError, ValueError):
        return default


def query_bool(name: str, default: bool) -> bool:
    return query_str(name, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


def bearing_deg(from_lat: float, from_lon: float, to_lat: float, to_lon: float) -> float:
    lat1 = radians(from_lat)
    lat2 = radians(to_lat)
    delta_lon = radians(to_lon - from_lon)
    y = sin(delta_lon) * cos(lat2)
    x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(delta_lon)
    return (degrees(atan2(y, x)) + 360) % 360


def nearest_waypoint(waypoints: pd.DataFrame, lat: float, lon: float) -> pd.Series | None:
    """Nearest waypoint, vectorized. Planar approximation is fine for NES-LTER."""
    if waypoints.empty:
        return None
    mean_lat = np.radians((lat + waypoints["decimalLatitude"]) / 2)
    dlat_nm = (waypoints["decimalLatitude"] - lat) * 60
    dlon_nm = (waypoints["decimalLongitude"] - lon) * 60 * np.cos(mean_lat)
    distances = (dlat_nm**2 + dlon_nm**2) ** 0.5
    return waypoints.loc[distances.idxmin()]


def ship_icon(course_deg: object) -> folium.DivIcon:
    course = 0.0 if pd.isna(course_deg) else float(course_deg)
    html = f"""
    <div style="
        transform: translate(-12px, -12px) rotate({course}deg);
        width: 24px;
        height: 24px;
        display: flex;
        align-items: center;
        justify-content: center;
        filter: drop-shadow(0 1px 2px rgba(0,0,0,0.65));
    ">
      <svg width="24" height="24" viewBox="0 0 24 24" aria-label="ship">
        <path d="M12 1.8 L20.5 21.5 L12 17.2 L3.5 21.5 Z" fill="#e11d48" stroke="white" stroke-width="1.8"/>
        <path d="M12 5 L15.2 16.8 L12 15.2 L8.8 16.8 Z" fill="white" opacity="0.85"/>
      </svg>
    </div>
    """
    return folium.DivIcon(html=html, icon_size=(24, 24), icon_anchor=(12, 12))


BASEMAPS = {
    "NOAA nautical charts / detailed bathymetry": {
        "tiles": "https://tileservice.charts.noaa.gov/tiles/50000_1/{z}/{x}/{y}.png",
        "attr": "NOAA Office of Coast Survey",
    },
    "Esri Ocean Basemap": {
        "tiles": "https://server.arcgisonline.com/ArcGIS/rest/services/Ocean/World_Ocean_Base/MapServer/tile/{z}/{y}/{x}",
        "attr": "Esri, Garmin, GEBCO, NOAA NGDC, and other contributors",
    },
    "OpenStreetMap": {"tiles": "OpenStreetMap", "attr": None},
}
DEFAULT_BASEMAP = "NOAA nautical charts / detailed bathymetry"

st.set_page_config(page_title="SHARP Tracker", layout="wide")
st.title("SHARP Tracker")

if "basemap" not in st.session_state:
    saved_basemap = query_str("basemap", DEFAULT_BASEMAP)
    st.session_state.basemap = saved_basemap if saved_basemap in BASEMAPS else DEFAULT_BASEMAP
if "show_seamarks" not in st.session_state:
    st.session_state.show_seamarks = query_bool("seamarks", True)
if "map_zoom" not in st.session_state:
    st.session_state.map_zoom = query_int("zoom", 13)
if "map_center_lat" not in st.session_state:
    st.session_state.map_center_lat = query_float("lat", float("nan"))
if "map_center_lon" not in st.session_state:
    st.session_state.map_center_lon = query_float("lon", float("nan"))


# Only read recent history: an unbounded full-table scan gets slow as the DB
# grows. Override with DASHBOARD_WINDOW_HOURS (and the marker caps below).
DATA_WINDOW_HOURS = float(os.getenv("DASHBOARD_WINDOW_HOURS", str(7 * 24)))
MAX_TRACK_POINTS = int(os.getenv("MAX_TRACK_POINTS", "800"))
MAX_MARKERS = int(os.getenv("MAX_MARKERS", "300"))


def load_positions(since) -> pd.DataFrame:
    try:
        return fetch_positions_df(since=since)
    except Exception as exc:
        st.error(f"Could not read database {db_label()}: {exc}")
        return pd.DataFrame()


@st.cache_data(ttl=5)
def cached_positions() -> pd.DataFrame:
    since = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=DATA_WINDOW_HOURS)
    return load_positions(since)


@st.cache_data(ttl=3600)
def load_current_waypoints() -> pd.DataFrame:
    response = requests.get(WAYPOINTS_URL, timeout=15)
    response.raise_for_status()
    waypoints = pd.read_csv(StringIO(response.text))
    waypoints = waypoints[waypoints["endDate"].astype(str).str.lower().eq("current")].copy()
    waypoints = waypoints.dropna(subset=["decimalLatitude", "decimalLongitude"])
    waypoints["decimalLatitude"] = waypoints["decimalLatitude"].astype(float)
    waypoints["decimalLongitude"] = waypoints["decimalLongitude"].astype(float)
    return waypoints.sort_values(["stationfullname", "station"])



with st.sidebar:
    st.header("Data")
    st.caption(f"Database: `{db_label()}`")
    st.caption(f"Reading last {DATA_WINDOW_HOURS:g}h of fixes")
    auto_refresh = st.toggle("Auto-refresh every 10 seconds", value=True)
    if st.button("Refresh now"):
        st.cache_data.clear()
        st.rerun()

if auto_refresh:
    # Use a Streamlit rerun rather than browser/window.location reload. This
    # preserves session_state, so streamlit-folium can reuse the last map
    # center/zoom instead of resetting after each refresh.
    st_autorefresh(interval=10_000, key="position_data_refresh")

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
    show_waypoints = st.checkbox("Show current NES-LTER waypoints", value=True)

    st.header("Map")
    selected_basemap = st.selectbox(
        "Basemap",
        list(BASEMAPS.keys()),
        key="basemap",
    )
    show_seamarks = st.checkbox("Show OpenSeaMap seamarks", key="show_seamarks")

st.query_params["basemap"] = selected_basemap
st.query_params["seamarks"] = "1" if show_seamarks else "0"

filtered = df[(df["display_time"] >= start_time) & (df["display_time"] <= end_time)].copy()

with st.sidebar:
    st.header("Summary")
    st.metric("Total fixes", len(df))
    st.metric("Displayed fixes", len(filtered))
    st.metric("First displayed", filtered["display_time"].min().strftime("%H:%M:%S") if not filtered.empty else "—")
    st.metric("Last displayed", filtered["display_time"].max().strftime("%H:%M:%S") if not filtered.empty else "—")

if filtered.empty:
    st.warning("No positions match the selected timestamp range.")
    st.stop()

latest = filtered.sort_values("display_time").iloc[-1]

try:
    waypoints = load_current_waypoints()
except Exception as exc:
    waypoints = pd.DataFrame()
    st.warning(f"Could not load NES-LTER waypoints: {exc}")

reference_waypoint = nearest_waypoint(waypoints, latest["latitude"], latest["longitude"])
ship_pointer_course = latest["course_deg"]
if reference_waypoint is not None:
    # Rotate the icon so its stern/base points back toward the nearest/current
    # waypoint and its bow points away from that waypoint.
    ship_pointer_course = bearing_deg(
        reference_waypoint["decimalLatitude"],
        reference_waypoint["decimalLongitude"],
        latest["latitude"],
        latest["longitude"],
    )

status_cols = st.columns(4)
status_cols[0].metric("SOG", format_value(latest["speed_knots"], " kn"))
status_cols[1].metric("COG", format_value(latest["course_deg"], "°", decimals=0))
status_cols[2].metric("Latest fix", latest["display_time"].strftime("%Y-%m-%d %H:%M:%S UTC"))
status_cols[3].metric("Reference waypoint", reference_waypoint["station"] if reference_waypoint is not None else "—")

default_center = [filtered["latitude"].mean(), filtered["longitude"].mean()]
if pd.isna(st.session_state.map_center_lat) or pd.isna(st.session_state.map_center_lon):
    center = default_center
else:
    center = [st.session_state.map_center_lat, st.session_state.map_center_lon]
zoom = st.session_state.map_zoom

map_obj = folium.Map(location=center, zoom_start=zoom, tiles=None)
for name, config in BASEMAPS.items():
    folium.TileLayer(
        tiles=config["tiles"],
        attr=config["attr"],
        name=name,
        overlay=False,
        control=True,
        show=name == selected_basemap,
    ).add_to(map_obj)

folium.TileLayer(
    tiles="https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png",
    attr="OpenSeaMap contributors",
    name="OpenSeaMap seamarks",
    overlay=True,
    control=True,
    show=show_seamarks,
).add_to(map_obj)

# Decimate heavy layers: the full window at 1 Hz can be tens of thousands of
# points, which bloats the map JSON rebuilt on every refresh.
track_points = filtered
if len(track_points) > MAX_TRACK_POINTS:
    track_points = track_points.iloc[:: len(track_points) // MAX_TRACK_POINTS]
line_coords = track_points[["latitude", "longitude"]].values.tolist()

shown_points = filtered
if len(shown_points) > MAX_MARKERS:
    shown_points = shown_points.iloc[:: len(shown_points) // MAX_MARKERS]

if show_waypoints and not waypoints.empty:
    waypoint_group = folium.FeatureGroup(name="Current NES-LTER waypoints", show=True)
    for waypoint in waypoints.itertuples(index=False):
        popup = folium.Popup(
            f"<b>{waypoint.station}</b><br>"
            f"Full name: {waypoint.stationfullname}<br>"
            f"Lat: {waypoint.decimalLatitude:.5f}<br>Lon: {waypoint.decimalLongitude:.5f}<br>"
            f"Depth: {waypoint.depth_m if pd.notna(waypoint.depth_m) else '—'} m",
            max_width=320,
        )
        folium.CircleMarker(
            [waypoint.decimalLatitude, waypoint.decimalLongitude],
            radius=5,
            color="#f59e0b",
            weight=2,
            fill=True,
            fill_color="#fde68a",
            fill_opacity=0.9,
            popup=popup,
            tooltip=f"NES-LTER {waypoint.station}",
        ).add_to(waypoint_group)
        folium.Marker(
            [waypoint.decimalLatitude, waypoint.decimalLongitude],
            icon=folium.DivIcon(
                html=f'<div style="font-size: 11px; font-weight: 700; color: #78350f; text-shadow: 0 0 3px white;">{waypoint.station}</div>',
                icon_size=(50, 14),
                icon_anchor=(-6, 6),
            ),
        ).add_to(waypoint_group)
    waypoint_group.add_to(map_obj)

# Ship position, track, and points change on every auto-refresh. Keeping them
# out of the base map and feeding them to st_folium via feature_group_to_add
# lets the frontend update markers in place instead of remounting the whole
# map component (which would otherwise reset the user's pan/zoom/baselayer).
live_group = folium.FeatureGroup(name="Live track", show=True)

latest_coords = (float(filtered["latitude"].iloc[-1]), float(filtered["longitude"].iloc[-1]))

if reference_waypoint is not None:
    folium.PolyLine(
        [[reference_waypoint["decimalLatitude"], reference_waypoint["decimalLongitude"]], latest_coords],
        color="#f59e0b",
        weight=2,
        opacity=0.75,
        dash_array="6,6",
        tooltip=f"Reference waypoint: {reference_waypoint['station']}",
    ).add_to(live_group)

if show_track and len(line_coords) > 1:
    folium.PolyLine(line_coords, color="#ff4b4b", weight=3, opacity=0.85, tooltip="Track").add_to(live_group)

if show_points:
    # Lightweight circles + tooltip only; per-point HTML popups make the
    # serialized map far too heavy to rebuild every refresh.
    for row in shown_points.itertuples(index=False):
        label = row.display_time.strftime("%Y-%m-%d %H:%M:%S UTC")
        folium.CircleMarker(
            [row.latitude, row.longitude],
            radius=4,
            color="#1f77b4",
            fill=True,
            fill_opacity=0.8,
            tooltip=f"{label} | SOG {format_value(row.speed_knots, ' kn')} | COG {format_value(row.course_deg, '°', decimals=0)}",
        ).add_to(live_group)

folium.Marker(
    latest_coords,
    tooltip=f"Latest displayed position — COG {format_value(latest['course_deg'], '°', decimals=0)}, SOG {format_value(latest['speed_knots'], ' kn')}",
    popup=folium.Popup(
        f"<b>Latest displayed position</b><br>"
        f"Time: {latest['display_time'].strftime('%Y-%m-%d %H:%M:%S UTC')}<br>"
        f"Lat: {latest['latitude']:.6f}<br>Lon: {latest['longitude']:.6f}<br>"
        f"SOG: {format_value(latest['speed_knots'], ' kn')}<br>"
        f"COG: {format_value(latest['course_deg'], '°', decimals=0)}",
        max_width=320,
    ),
    icon=ship_icon(ship_pointer_course),
).add_to(live_group)

layer_control = folium.LayerControl()

# Deliberately omit zoom=/center= here. Passing them on every render would
# reassert the *previous* render's (stale) session_state values on exactly
# the rerun triggered by a user's pan/zoom, snapping the view back before
# the new position is saved. folium.Map's location/zoom_start above already
# seeds the correct view whenever the map does need to (re)mount.
map_state = st_folium(
    map_obj,
    width=None,
    height=650,
    returned_objects=["zoom", "center"],
    feature_group_to_add=live_group,
    layer_control=layer_control,
)

# Adopt the user's live pan/zoom into session_state, then force an immediate
# rerun so the next render's folium.Map is seeded with it right away. Without
# this, the update only takes effect on whatever rerun happens to come next
# (e.g. the 10s auto-refresh) — and if that rerun starts before this one
# finishes, it can render with the stale value and visibly snap the map back.
# A tolerance on center avoids reacting to Leaflet's own sub-pixel rounding
# noise, which would otherwise trigger a rerun on every single render.
CENTER_CHANGE_TOLERANCE_DEG = 1e-5
map_view_changed = False
if map_state:
    if map_state.get("zoom") is not None and int(map_state["zoom"]) != st.session_state.map_zoom:
        st.session_state.map_zoom = int(map_state["zoom"])
        map_view_changed = True
    if map_state.get("center"):
        new_lat = float(map_state["center"]["lat"])
        new_lon = float(map_state["center"]["lng"])
        if (
            pd.isna(st.session_state.map_center_lat)
            or pd.isna(st.session_state.map_center_lon)
            or abs(new_lat - st.session_state.map_center_lat) > CENTER_CHANGE_TOLERANCE_DEG
            or abs(new_lon - st.session_state.map_center_lon) > CENTER_CHANGE_TOLERANCE_DEG
        ):
            st.session_state.map_center_lat = new_lat
            st.session_state.map_center_lon = new_lon
            map_view_changed = True

if map_view_changed:
    st.query_params["zoom"] = str(st.session_state.map_zoom)
    st.query_params["lat"] = f"{st.session_state.map_center_lat:.7f}"
    st.query_params["lon"] = f"{st.session_state.map_center_lon:.7f}"
    st.rerun()

with st.expander("Recent displayed fixes"):
    st.dataframe(filtered.tail(100).sort_values("display_time", ascending=False), width="stretch")
