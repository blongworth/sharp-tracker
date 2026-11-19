// Unit tests for EtaModel (run with `make test`). The sample responses come
// from resources/jsonData/eta_sample.json, real /api/eta output that
// tests/test_server.py keeps in step with the server.

import Toybox.Application;
import Toybox.Communications;
import Toybox.Lang;
import Toybox.Test;

// Helper, not a test: (:debug) keeps it out of release builds without making
// the runner treat it as a test.
(:debug)
function sample(name as String) as Dictionary {
    var all = Application.loadResource(Rez.JsonData.EtaSample) as Dictionary;
    return all[name] as Dictionary;
}

(:test)
function parsesStationTarget(logger as Test.Logger) as Boolean {
    var m = EtaModel.parseEta(sample("station")) as Dictionary;
    Test.assertEqual(m["name"], "L5");
    Test.assertEqual(m["auto"], false);
    Test.assertEqual(m["etaEpoch"], 1795116556);
    Test.assertEqual(m["fixEpoch"], 1795094505);
    Test.assertEqual(m["stale"], false);
    // Counted from the fix time, TTG is the server's ttg_s (22051 s ~ 6:08).
    Test.assertEqual(EtaModel.glanceLine(m, 1795094505), "L5 6:08 61 nm");
    return true;
}

(:test)
function parsesAutoTarget(logger as Test.Logger) as Boolean {
    var m = EtaModel.parseEta(sample("auto")) as Dictionary;
    Test.assertEqual(m["name"], "u1a");
    Test.assertEqual(m["auto"], true);
    return true;
}

(:test)
function nothingAheadShowsReason(logger as Test.Logger) as Boolean {
    var m = EtaModel.parseEta(sample("nothing_ahead")) as Dictionary;
    Test.assert(m["name"] == null);
    Test.assert(m["etaEpoch"] == null);
    Test.assertEqual(EtaModel.glanceLine(m, 0), "no station ahead");
    return true;
}

(:test)
function noFixHasNoTtgAndIsStale(logger as Test.Logger) as Boolean {
    var m = EtaModel.parseEta(sample("no_fix")) as Dictionary;
    Test.assertEqual(EtaModel.glanceLine(m, 0), "L5 -- -- nm");
    Test.assert(EtaModel.isStale(m["fixEpoch"], 0, m["stale"]));
    return true;
}

(:test)
function rejectsBadResponses(logger as Test.Logger) as Boolean {
    Test.assertEqual(EtaModel.parseEta(null), "Bad response");
    Test.assertEqual(EtaModel.parseEta("<html>"), "Bad response");
    Test.assertEqual(EtaModel.parseEta({"target" => null}), "Bad response");
    Test.assertEqual(EtaModel.glanceLine(null, 0), "No data yet");
    return true;
}

(:test)
function formatsTimeToGo(logger as Test.Logger) as Boolean {
    Test.assertEqual(EtaModel.formatTtg(4906), "1:22");
    Test.assertEqual(EtaModel.formatTtg(420), "0:07");
    Test.assertEqual(EtaModel.formatTtg(36000), "10:00");
    Test.assertEqual(EtaModel.formatTtg(0), "due");
    Test.assertEqual(EtaModel.formatTtg(-5), "due");
    Test.assertEqual(EtaModel.formatTtg(null), "--");
    Test.assertEqual(EtaModel.remainingSeconds(1000, 400), 600);
    Test.assert(EtaModel.remainingSeconds(null, 400) == null);
    return true;
}

(:test)
function formatsDistance(logger as Test.Logger) as Boolean {
    Test.assertEqual(EtaModel.formatDistance(6.04), "6.0 nm");
    Test.assertEqual(EtaModel.formatDistance(6), "6.0 nm");
    Test.assertEqual(EtaModel.formatDistance(61.25), "61 nm");
    Test.assertEqual(EtaModel.formatDistance(null), "-- nm");
    return true;
}

(:test)
function staleness(logger as Test.Logger) as Boolean {
    Test.assert(!EtaModel.isStale(1000, 1100, false));
    Test.assert(EtaModel.isStale(1000, 1400, false));  // older than 300 s
    Test.assert(EtaModel.isStale(1000, 1001, true));   // server says stale
    Test.assert(EtaModel.isStale(null, 1001, false));  // no fix
    return true;
}

(:test)
function errorTexts(logger as Test.Logger) as Boolean {
    Test.assert(EtaModel.errorText(200) == null);
    Test.assertEqual(EtaModel.errorText(404), "Unknown station");
    Test.assertEqual(EtaModel.errorText(503), "No station list");
    Test.assertEqual(EtaModel.errorText(500), "HTTP 500");
    Test.assertEqual(EtaModel.errorText(Communications.BLE_CONNECTION_UNAVAILABLE), "No phone");
    Test.assertEqual(EtaModel.errorText(Communications.SECURE_CONNECTION_REQUIRED), "Needs https://");
    Test.assertEqual(EtaModel.errorText(-12345), "Error -12345");
    return true;
}
