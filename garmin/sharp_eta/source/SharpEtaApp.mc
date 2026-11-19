// SHARP ETA: time to go / ETA to the next NES-LTER station (or a chosen
// one) from the SHARP Tracker server's /api/eta, shown as a glance and a
// widget on a Forerunner 165.

import Toybox.Application;
import Toybox.Lang;
import Toybox.WatchUi;

(:glance)
class SharpEtaApp extends Application.AppBase {

    function initialize() {
        AppBase.initialize();
    }

    function getInitialView() {
        var view = new EtaView();
        return [view, new EtaDelegate(view)];
    }

    function getGlanceView() {
        return [new EtaGlanceView()];
    }

    // Settings changed in Garmin Connect: drop data from the old server/speed.
    function onSettingsChanged() as Void {
        Application.Storage.deleteValue(KEY_ETA);
        Application.Storage.deleteValue(KEY_STATIONS);
        WatchUi.requestUpdate();
    }
}
