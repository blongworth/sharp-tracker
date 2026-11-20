// Pure logic for the SHARP ETA watch app: parsing /api/eta responses and
// formatting. No UI, network, or storage calls, so source/test/EtaModelTest.mc
// can exercise all of it. Available to the glance as well as the main view.

import Toybox.Communications;
import Toybox.Lang;

(:glance)
module EtaModel {

    // Data older than this (seconds since the fix) is shown dimmed.
    const STALE_SECONDS = 300;

    // A /api/eta response -> a flat model Dictionary, or a String error.
    // Keeps only what the watch shows, so the cached copy in Storage stays small.
    function parseEta(data) {
        if (!(data instanceof Dictionary) || !data.hasKey("target") || !data.hasKey("eta_epoch")) {
            return "Bad response";
        }
        var target = data["target"];
        var name = null;
        var auto = false;
        if (target instanceof Dictionary) {
            name = target["station"] != null ? target["station"] : target["name"];
            auto = "auto".equals(target["mode"]);
        }
        // Null values are left out (a missing key reads as null), so the model
        // can go into Application.Storage without relying on stored nulls.
        var model = {"auto" => auto, "stale" => data["stale"] == true};
        putIfSet(model, "name", name);
        putIfSet(model, "reason", data["reason"]);
        putIfSet(model, "etaEpoch", data["eta_epoch"]);
        putIfSet(model, "fixEpoch", data["fix_epoch"]);
        putIfSet(model, "distanceNm", data["distance_nm"]);
        putIfSet(model, "speedKn", data["speed_kn"]);
        putIfSet(model, "vmgKn", data["vmg_kn"]);
        return model;
    }

    function putIfSet(dict as Dictionary, key as String, value) as Void {
        if (value != null) {
            dict[key] = value;
        }
    }

    // Seconds from now until etaEpoch, or null when there is no ETA.
    function remainingSeconds(etaEpoch, nowEpoch) {
        if (etaEpoch == null) {
            return null;
        }
        return etaEpoch - nowEpoch;
    }

    // Distance to go now, dead-reckoned from the fix: the ship closes on the
    // target at vmgKn (speed made good toward it at the fix), or moves away
    // when that is negative. Without a VMG, the distance at the fix.
    function estimateDistance(model as Dictionary?, nowEpoch) {
        if (model == null || model["distanceNm"] == null) {
            return null;
        }
        var dist = model["distanceNm"].toFloat();
        var vmg = model["vmgKn"];
        var fix = model["fixEpoch"];
        if (vmg == null || fix == null || nowEpoch <= fix) {
            return dist;
        }
        dist -= vmg.toFloat() * (nowEpoch - fix) / 3600.0;
        return dist > 0 ? dist : 0.0;
    }

    // Seconds to go now: the dead-reckoned distance at the server's speed basis
    // (planned speed or recent mean SOG). Steaming straight at the target
    // (VMG = speed) this counts down one second per second; off course it
    // counts down slower, and heading away it counts up. Without a VMG it
    // counts down from the server's ETA as before.
    function estimateTtg(model as Dictionary?, nowEpoch) {
        if (model == null || model["etaEpoch"] == null) {
            return null;
        }
        var speed = model["speedKn"];
        if (model["vmgKn"] == null || speed == null || speed.toFloat() <= 0) {
            return remainingSeconds(model["etaEpoch"], nowEpoch);
        }
        var dist = estimateDistance(model, nowEpoch);
        if (dist == null) {
            return remainingSeconds(model["etaEpoch"], nowEpoch);
        }
        return (dist / speed.toFloat() * 3600 + 0.5).toNumber();
    }

    // 4906 -> "1:22", 420 -> "0:07", <= 0 -> "due", null -> "--".
    function formatTtg(seconds) {
        if (seconds == null) {
            return "--";
        }
        if (seconds <= 0) {
            return "due";
        }
        var minutes = (seconds + 30) / 60;
        return (minutes / 60).format("%d") + ":" + (minutes % 60).format("%02d");
    }

    // 6.04 -> "6.0 nm", 61.25 -> "61 nm", null -> "-- nm".
    function formatDistance(nm) {
        if (nm == null) {
            return "-- nm";
        }
        var d = nm.toFloat();  // JSON may give a Number for whole values
        if (d < 10) {
            return d.format("%.1f") + " nm";
        }
        return d.format("%.0f") + " nm";
    }

    // Stale when the server says so, there is no fix, or the fix is old.
    function isStale(fixEpoch, nowEpoch, staleFlag) {
        return staleFlag == true || fixEpoch == null || nowEpoch - fixEpoch > STALE_SECONDS;
    }

    // "L5 1:22 6.0 nm" for the glance; the reason when there is no target.
    function glanceLine(model as Dictionary?, nowEpoch) {
        if (model == null) {
            return "No data yet";
        }
        if (model["name"] == null) {
            return model["reason"] != null ? model["reason"] : "No target";
        }
        return model["name"] + " " + formatTtg(estimateTtg(model, nowEpoch))
            + " " + formatDistance(estimateDistance(model, nowEpoch));
    }

    // makeWebRequest response code -> short text for the screen.
    function errorText(code) {
        if (code == 200) {
            return null;
        }
        if (code == Communications.BLE_CONNECTION_UNAVAILABLE) {
            return "No phone";
        }
        if (code == Communications.NETWORK_REQUEST_TIMED_OUT || code == Communications.BLE_HOST_TIMEOUT) {
            return "Server timeout";
        }
        if (code == Communications.SECURE_CONNECTION_REQUIRED) {
            return "Needs https://";
        }
        if (code == 404) {
            return "Unknown station";
        }
        if (code == 503) {
            return "No station list";
        }
        if (code > 0) {
            return "HTTP " + code;
        }
        return "Error " + code;
    }
}
