// Glance (the one-line entry in the widget loop): the cached target, time to
// go counted down from the cached ETA, and distance. It never fetches; the
// main view does, so after a long time away from the phone the glance shows
// old data, dimmed.

import Toybox.Application;
import Toybox.Graphics;
import Toybox.Lang;
import Toybox.Time;
import Toybox.WatchUi;

(:glance)
class EtaGlanceView extends WatchUi.GlanceView {

    function initialize() {
        GlanceView.initialize();
    }

    function onUpdate(dc as Dc) as Void {
        var now = Time.now().value();
        var model = Application.Storage.getValue("eta") as Dictionary?;
        var stale = model == null || EtaModel.isStale(model["fixEpoch"], now, model["stale"]);
        dc.setColor(Graphics.COLOR_ORANGE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(0, 0, Graphics.FONT_GLANCE, "SHARP ETA", Graphics.TEXT_JUSTIFY_LEFT);
        dc.setColor(stale ? Graphics.COLOR_LT_GRAY : Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(0, dc.getHeight() / 2, Graphics.FONT_GLANCE, EtaModel.glanceLine(model, now),
            Graphics.TEXT_JUSTIFY_LEFT);
    }
}
