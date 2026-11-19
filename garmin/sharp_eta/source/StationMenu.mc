// Target picker: "Auto (ahead)" plus the cached station list, nearest first.
// The choice is stored per watch (Application.Storage), like the per-browser
// target on the web pages.

import Toybox.Application;
import Toybox.Lang;
import Toybox.WatchUi;

module StationMenu {

    const AUTO_ID = "__auto__";

    function open(view) {
        var menu = new WatchUi.Menu2({:title => "Target"});
        var current = Application.Storage.getValue(KEY_STATION);
        menu.addItem(new WatchUi.MenuItem("Auto (ahead)", current == null ? "selected" : null, AUTO_ID, null));
        var stations = Application.Storage.getValue(KEY_STATIONS) as Array<Array>?;
        if (stations == null || stations.size() == 0) {
            menu.addItem(new WatchUi.MenuItem("No station list yet", "check connection", AUTO_ID, null));
        } else {
            for (var i = 0; i < stations.size(); i++) {
                var id = stations[i][0];
                var sub = id.equals(current) ? "selected" : stations[i][1];
                menu.addItem(new WatchUi.MenuItem(id, sub, id, null));
            }
        }
        WatchUi.pushView(menu, new StationMenuDelegate(view), WatchUi.SLIDE_UP);
    }
}

class StationMenuDelegate extends WatchUi.Menu2InputDelegate {

    private var _view;

    function initialize(view) {
        Menu2InputDelegate.initialize();
        _view = view;
    }

    function onSelect(item as WatchUi.MenuItem) as Void {
        var id = item.getId();
        if (StationMenu.AUTO_ID.equals(id)) {
            Application.Storage.deleteValue(KEY_STATION);
        } else {
            Application.Storage.setValue(KEY_STATION, id);
        }
        // The cached ETA is for the old target: drop it so nothing stale shows.
        Application.Storage.deleteValue(KEY_ETA);
        WatchUi.popView(WatchUi.SLIDE_DOWN);
        _view.refresh(false);
    }
}
