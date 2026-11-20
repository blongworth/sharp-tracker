// Main widget view: target name, a large time to go re-estimated every
// second from the last fix and its VMG (EtaModel.estimateTtg), local ETA,
// distance, and data age or error.
// Fetches on open and every REFRESH_SECONDS while open. SELECT opens the
// station menu.

import Toybox.Application;
import Toybox.Graphics;
import Toybox.Lang;
import Toybox.Time;
import Toybox.Time.Gregorian;
import Toybox.Timer;
import Toybox.WatchUi;

const REFRESH_SECONDS = 60;

class EtaView extends WatchUi.View {

    private var _client;
    private var _timer;
    private var _ticks = 0;

    function initialize() {
        View.initialize();
        _client = new EtaClient(method(:onFetched));
    }

    function onShow() as Void {
        refresh(true);
        _timer = new Timer.Timer();
        _timer.start(method(:onTick), 1000, true);
    }

    function onHide() as Void {
        if (_timer != null) {
            _timer.stop();
            _timer = null;
        }
    }

    // stations: also refresh the station list (on open and after a menu choice).
    function refresh(stations) {
        _ticks = 0;
        _client.fetchEta();
        if (stations) {
            _client.fetchStations();
        }
    }

    function onTick() as Void {
        _ticks++;
        if (_ticks >= REFRESH_SECONDS) {
            refresh(false);
        }
        WatchUi.requestUpdate();  // the countdown moves every second
    }

    function onFetched() as Void {
        WatchUi.requestUpdate();
    }

    function onUpdate(dc as Dc) as Void {
        var now = Time.now().value();
        var model = Application.Storage.getValue(KEY_ETA) as Dictionary?;
        var error = Application.Storage.getValue(KEY_ERROR);
        var w = dc.getWidth();
        var h = dc.getHeight();
        var cx = w / 2;

        dc.setColor(Graphics.COLOR_BLACK, Graphics.COLOR_BLACK);
        dc.clear();

        if (model == null) {
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(cx, h / 2, Graphics.FONT_SMALL, error != null ? error : "Loading...",
                Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
            return;
        }

        var stale = EtaModel.isStale(model["fixEpoch"], now, model["stale"]);
        var main = stale ? Graphics.COLOR_LT_GRAY : Graphics.COLOR_WHITE;

        // Target: "L5" or "Auto: u1a", or why there is none.
        var title = model["name"];
        if (title == null) {
            title = model["reason"] != null ? model["reason"] : "No target";
        } else if (model["auto"]) {
            title = "Auto: " + title;
        }
        dc.setColor(Graphics.COLOR_ORANGE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(cx, h * 0.16, Graphics.FONT_SMALL, title, Graphics.TEXT_JUSTIFY_CENTER);

        // Time to go (h:mm), re-estimated every second between fixes and fetches.
        var ttgSeconds = EtaModel.estimateTtg(model, now);
        dc.setColor(main, Graphics.COLOR_TRANSPARENT);
        dc.drawText(cx, h * 0.42, Graphics.FONT_NUMBER_HOT, EtaModel.formatTtg(ttgSeconds),
            Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);

        dc.drawText(cx, h * 0.60, Graphics.FONT_SMALL,
            "ETA " + localTime(ttgSeconds == null ? null : now + ttgSeconds) + "  "
                + EtaModel.formatDistance(EtaModel.estimateDistance(model, now)),
            Graphics.TEXT_JUSTIFY_CENTER);

        // Bottom line: the last error, else how old the fix is.
        dc.setColor(error != null ? Graphics.COLOR_RED : Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        var footer = error;
        if (footer == null) {
            var fix = model["fixEpoch"];
            footer = fix == null ? "no position yet" : "fix " + ageText(now - fix) + " ago";
        }
        dc.drawText(cx, h * 0.76, Graphics.FONT_XTINY, footer, Graphics.TEXT_JUSTIFY_CENTER);
    }

    private function localTime(epoch) {
        if (epoch == null) {
            return "--:--";
        }
        var info = Gregorian.info(new Time.Moment(epoch), Time.FORMAT_SHORT);
        return info.hour.format("%02d") + ":" + info.min.format("%02d");
    }

    private function ageText(seconds) {
        if (seconds < 90) {
            return seconds.format("%d") + " s";
        }
        if (seconds < 5400) {
            return (seconds / 60).format("%d") + " min";
        }
        return (seconds / 3600).format("%d") + " h";
    }
}

class EtaDelegate extends WatchUi.BehaviorDelegate {

    private var _view;

    function initialize(view) {
        BehaviorDelegate.initialize();
        _view = view;
    }

    function onSelect() as Boolean {
        StationMenu.open(_view);
        return true;
    }

    function onMenu() as Boolean {
        return onSelect();
    }
}
