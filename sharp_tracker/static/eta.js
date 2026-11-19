// Shared by the map (index.html) and the time-to-go page (eta.html): the
// viewer's chosen target and planned speed (kept per browser), the /api/eta
// URLs, and formatting. Plain script, no build step.
"use strict";

const ETA_TARGET_KEY = "etaTarget";  // {mode: "auto"} | {mode: "station", station} | {mode: "point", lat, lon, name}
const ETA_SPEED_KEY = "etaSpeedKn";  // planned speed in knots, or absent for recent mean SOG

// Storage can be missing or throw (private windows, blocked site data):
// fall back to auto / mean SOG and keep working.
function loadTarget() {
  try {
    const t = JSON.parse(localStorage.getItem(ETA_TARGET_KEY));
    if (t && (t.mode === "station" || t.mode === "point")) return t;
  } catch (e) { /* ignore */ }
  return { mode: "auto" };
}

function saveTarget(t) {
  try { localStorage.setItem(ETA_TARGET_KEY, JSON.stringify(t)); } catch (e) { /* ignore */ }
}

function loadSpeed() {
  try {
    const v = parseFloat(localStorage.getItem(ETA_SPEED_KEY));
    return v > 0 ? v : null;
  } catch (e) { return null; }
}

function saveSpeed(v) {
  try {
    if (v > 0) localStorage.setItem(ETA_SPEED_KEY, String(v));
    else localStorage.removeItem(ETA_SPEED_KEY);
  } catch (e) { /* ignore */ }
}

function etaUrl(target, speed) {
  const q = new URLSearchParams();
  if (target.mode === "station") q.set("station", target.station);
  if (target.mode === "point") {
    q.set("lat", target.lat.toFixed(5));
    q.set("lon", target.lon.toFixed(5));
    if (target.name) q.set("name", target.name);
  }
  if (speed) q.set("speed_kn", String(speed));
  const s = q.toString();
  return "/api/eta" + (s ? "?" + s : "");
}

function stationsUrl(speed) {
  return "/api/eta/stations" + (speed ? "?speed_kn=" + speed : "");
}

// Seconds left until an ETA (epoch seconds), counting down from now.
function remainingS(etaEpoch) {
  return etaEpoch === null || etaEpoch === undefined ? null : etaEpoch - Date.now() / 1000;
}

function fmtTtg(s) {
  if (s === null || s === undefined || Number.isNaN(s)) return "—";
  if (s <= 0) return "due";
  const m = Math.round(s / 60);
  if (m < 60) return m + " m";
  return Math.floor(m / 60) + " h " + String(m % 60).padStart(2, "0") + " m";
}

function fmtEtaUtc(etaEpoch) {
  if (etaEpoch === null || etaEpoch === undefined) return "—";
  return new Date(etaEpoch * 1000).toISOString().slice(11, 16) + " UTC";
}

function fmtEtaLocal(etaEpoch) {
  if (etaEpoch === null || etaEpoch === undefined) return "";
  return new Date(etaEpoch * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) + " local";
}

function fmtNm(d) {
  return d === null || d === undefined ? "—" : d.toFixed(d < 10 ? 2 : 1) + " nm";
}

function targetLabel(r) {
  if (!r.target) return r.reason || "no target";
  const t = r.target;
  const prefix = t.mode === "auto" ? "Auto → " : "";
  return prefix + (t.station ? t.station + " (" + t.name + ")" : t.name);
}

function speedLabel(r) {
  if (r.speed_kn === null || r.speed_kn === undefined) return "no speed";
  const basis = r.speed_source === "planned" ? "planned" : "mean SOG";
  return r.speed_kn.toFixed(1) + " kn " + basis;
}
