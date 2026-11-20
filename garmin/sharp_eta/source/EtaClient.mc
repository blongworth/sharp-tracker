// Network and storage for the watch app: fetches /api/eta and
// /api/eta/stations through the paired phone (makeWebRequest goes over
// Bluetooth to Garmin Connect Mobile) and caches results in Application.Storage
// so the glance and the next launch have something to show.

import Toybox.Application;
import Toybox.Communications;
import Toybox.Lang;
import Toybox.System;
import Toybox.Time;

// DEBUG_SERVER_URL (used when no URL is set) comes from gen/DebugServer.mc,
// written by the Makefile: `make sim SERVER=...`, default http://localhost:8090.
// Release builds leave it empty.

// Storage keys
const KEY_ETA = "eta";            // parsed model (EtaModel.parseEta)
const KEY_FETCHED = "fetchedAt";  // epoch seconds of the last good /api/eta
const KEY_ERROR = "error";        // last error text, or null
const KEY_STATION = "station";    // chosen station id, or null for auto
const KEY_STATIONS = "stations";  // [[id, name], ...] nearest first

// Storage.setValue with a null value isn't documented as allowed: delete instead.
function storeOrClear(key, value) {
    if (value == null) {
        Application.Storage.deleteValue(key);
    } else {
        Application.Storage.setValue(key, value);
    }
}

// Response codes on the simulator console (debug builds only).
(:debug)
function logResponse(what, code) {
    System.println(what + " -> " + code);
}

(:release)
function logResponse(what, code) {
}

class EtaClient {

    private var _onDone;  // Method() called after every fetch, success or not
    private var _busy = false;     // an /api/eta request is outstanding
    private var _pending = false;  // another fetch was asked for meanwhile

    function initialize(onDone) {
        _onDone = onDone;
    }

    static function serverUrl() {
        var url = Application.Properties.getValue("serverUrl");
        if (url == null || url.equals("")) {
            url = DEBUG_SERVER_URL;
        }
        // Drop one trailing slash so "https://host/" works too.
        if (url.length() > 0 && url.substring(url.length() - 1, url.length()).equals("/")) {
            url = url.substring(0, url.length() - 1);
        }
        return url;
    }

    static function params() {
        var p = {};
        var station = Application.Storage.getValue(KEY_STATION);
        if (station != null) {
            p["station"] = station;
        }
        var speed = Application.Properties.getValue("plannedSpeedKn");
        if (speed != null && speed > 0) {
            p["speed_kn"] = speed.format("%.1f");
        }
        return p;
    }

    // One /api/eta request at a time: overlapping requests over a slow phone
    // link fail (BLE queue full). A fetch asked for while one is outstanding
    // (e.g. a new target from the menu) runs when it finishes.
    function fetchEta() {
        if (_busy) {
            _pending = true;
            return;
        }
        var base = serverUrl();
        if (base.equals("")) {
            Application.Storage.setValue(KEY_ERROR, "Set server URL in Garmin Connect");
            _onDone.invoke();
            return;
        }
        _busy = true;
        Communications.makeWebRequest(base + "/api/eta", params(), jsonOptions(), method(:onEta));
    }

    function fetchStations() {
        var base = serverUrl();
        if (base.equals("")) {
            return;
        }
        var p = params();
        p.remove("station");
        Communications.makeWebRequest(base + "/api/eta/stations", p, jsonOptions(), method(:onStations));
    }

    function onEta(code as Number, data as Dictionary or String or Null) as Void {
        logResponse("/api/eta", code);
        var error = EtaModel.errorText(code);
        if (error == null) {
            var model = EtaModel.parseEta(data);
            if (model instanceof String) {
                error = model;
            } else {
                Application.Storage.setValue(KEY_ETA, model);
                Application.Storage.setValue(KEY_FETCHED, Time.now().value());
            }
        }
        if (code == 404 && Application.Storage.getValue(KEY_STATION) != null) {
            // The chosen station is gone from the list: fall back to auto.
            Application.Storage.deleteValue(KEY_STATION);
        }
        storeOrClear(KEY_ERROR, error);
        _busy = false;
        if (_pending) {
            _pending = false;
            fetchEta();
            return;  // _onDone runs when that one finishes
        }
        _onDone.invoke();
    }

    function onStations(code as Number, data as Dictionary or String or Null) as Void {
        logResponse("/api/eta/stations", code);
        if (code != 200 || !(data instanceof Dictionary) || !(data["stations"] instanceof Array)) {
            return;
        }
        // Only id and name: Storage and the menu don't need the rest.
        var list = [];
        var rows = data["stations"] as Array;
        for (var i = 0; i < rows.size(); i++) {
            var row = rows[i];
            if (row instanceof Dictionary && row["station"] != null) {
                list.add([row["station"], row["name"]]);
            }
        }
        Application.Storage.setValue(KEY_STATIONS, list);
    }

    private function jsonOptions() {
        return {
            :method => Communications.HTTP_REQUEST_METHOD_GET,
            :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON
        };
    }
}
