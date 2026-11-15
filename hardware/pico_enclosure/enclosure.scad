// SHARP Tracker: 3D-printable enclosure for the Pico W NMEA collector.
//
// Box + screw-down lid. The Pico W and a perfboard with the opto-isolated
// NMEA 0183 input (see README.md) sit on standoffs. Ports:
//   front wall: DE-9 (DB9) male panel connector, GPS NMEA in (pins 2 + 5)
//   back wall:  panel-mount USB-C -> micro-USB cable, 5 V power + data
// The lid is held by four M3 self-tapping screws driven into the corner bosses,
// so it can be opened for BOOTSEL or rewiring. Firmware edits and the serial console work
// through the USB-C port without opening the box, if the panel cable carries data.
//
// Units: mm. Measure your actual panel connectors and adjust the [Ports] values
// before printing; panel-mount USB-C flanges vary between vendors.
//
// Render one part at a time for export: set `part` to "box" or "lid".

part = "both"; // [both, box, lid]

/* [Box] */
inner = [120, 62, 32];   // cavity x (front-back), y, z (floor to lid)
wall = 2.5;
floor_t = 2.5;
corner_r = 3;            // cavity corner radius
fit = 0.3;               // clearance between lid lip and walls

/* [Lid] */
lid_t = 2.5;
lip_h = 3;               // lip that drops inside the walls to locate the lid
lip_w = 1.6;
boss_d = 8;              // corner screw bosses
boss_hole_d = 2.6;       // pilot for M3 self-tapping screws (try 2.5 in PETG if loose)
boss_hole_depth = 12;
lid_screw_d = 3.4;       // M3 clearance

/* [Pico W] */
pico_size = [51, 21];
pico_holes = [[2, 4.8], [2, 16.2], [49, 4.8], [49, 16.2]];  // datasheet: 47 x 11.4 mm
pico_pos = [30, 5];      // USB end faces the back wall (x = 0)
pico_standoff_h = 5;     // raise to ~11 if the Pico has pin headers soldered on
standoff_d = 5;
standoff_hole_d = 1.8;   // M2 self-tapping

/* [Interface board] */
iface_size = [70, 30];   // 3 x 7 cm perfboard
iface_holes = [[2, 2], [2, 28], [68, 2], [68, 28]];
iface_pos = [30, 29];
iface_standoff_h = 5;

/* [Ports] */
port_z = 16;             // port centre height above the cavity floor
// DE-9 shell size E: cutout covered by the 30.8 x 12.5 mm flange
db9_y = 31;
db9_cut = [20.5, 11.5];
db9_screw_dx = 25;       // standard 24.99 mm
db9_screw_d = 3.2;
// USB-C panel mount: "flange" (rectangular flange with two screws) or "round" (threaded bulkhead)
usb_style = "flange";    // [flange, round]
usb_y = 31;
usb_cut = [11, 5];
usb_screw_dx = 20;       // MEASURE: varies by vendor
usb_screw_d = 3.2;
usb_round_d = 16;        // MEASURE: thread diameter for round bulkheads

/* [Mounting ears] */
ears = true;
ear_len = 12;
ear_w = 18;
ear_t = 4;
ear_slot_d = 4.5;        // #8 / M4 screw
ear_x = [24, 96];

$fn = 48;

// ---------------------------------------------------------------- helpers

module rbox(size, r) {
    r2 = max(min(r, size.x / 2, size.y / 2), 0.01);
    hull() for (x = [r2, size.x - r2], y = [r2, size.y - r2])
        translate([x, y, 0]) cylinder(r = r2, h = size.z);
}

boss_centers = [
    [boss_d / 2, boss_d / 2],
    [inner.x - boss_d / 2, boss_d / 2],
    [boss_d / 2, inner.y - boss_d / 2],
    [inner.x - boss_d / 2, inner.y - boss_d / 2],
];

// Corner boss footprint: a cylinder hulled into the cavity corner.
module boss_shape(c, extra, h) {
    sx = c.x < inner.x / 2 ? -1 : 1;
    sy = c.y < inner.y / 2 ? -1 : 1;
    hull() {
        translate([c.x, c.y, 0]) cylinder(d = boss_d + 2 * extra, h = h);
        translate([c.x + sx * boss_d / 4 - boss_d / 4, c.y + sy * boss_d / 4 - boss_d / 4, 0])
            cube([boss_d / 2 + extra, boss_d / 2 + extra, h]);
    }
}

// Cuts through the front (x = inner.x) or back (x = 0) wall.
function wall_x0(front) = front ? inner.x - 1 : -wall - 1;

module wall_rect(front, cy, w, h) {
    translate([wall_x0(front), cy - w / 2, port_z - h / 2]) cube([wall + 2, w, h]);
}

module wall_hole(front, cy, d) {
    translate([wall_x0(front), cy, port_z]) rotate([0, 90, 0]) cylinder(d = d, h = wall + 2);
}

module standoffs(pos, holes, h) {
    for (p = holes) translate([pos.x + p.x, pos.y + p.y, 0]) difference() {
        cylinder(d = standoff_d, h = h);
        translate([0, 0, 1]) cylinder(d = standoff_hole_d, h = h);
    }
}

module ear(x, side) {
    y0 = side < 0 ? -wall : inner.y + wall;
    translate([x, 0, -floor_t]) difference() {
        hull() {
            translate([-ear_w / 2, y0 - (side < 0 ? 0 : 1), 0]) cube([ear_w, 1, ear_t]);
            translate([0, y0 + side * (ear_len - ear_w / 4), 0]) cylinder(d = ear_w / 2 + 2, h = ear_t);
        }
        hull() for (dy = [ear_len / 2, ear_len - ear_w / 4])
            translate([0, y0 + side * dy, -1]) cylinder(d = ear_slot_d, h = ear_t + 2);
    }
}

// ---------------------------------------------------------------- parts

module box() {
    difference() {
        union() {
            translate([-wall, -wall, -floor_t])
                rbox([inner.x + 2 * wall, inner.y + 2 * wall, inner.z + floor_t], corner_r + wall);
            if (ears) for (x = ear_x, s = [-1, 1]) ear(x, s);
        }
        rbox([inner.x, inner.y, inner.z + 1], corner_r);

        // front: DB9 GPS input
        wall_rect(true, db9_y, db9_cut.x, db9_cut.y);
        for (s = [-1, 1]) wall_hole(true, db9_y + s * db9_screw_dx / 2, db9_screw_d);

        // back: USB-C power/data
        if (usb_style == "round") {
            wall_hole(false, usb_y, usb_round_d);
        } else {
            wall_rect(false, usb_y, usb_cut.x, usb_cut.y);
            for (s = [-1, 1]) wall_hole(false, usb_y + s * usb_screw_dx / 2, usb_screw_d);
        }
    }

    // lid screw bosses
    for (c = boss_centers) difference() {
        boss_shape(c, 0, inner.z);
        translate([c.x, c.y, inner.z - boss_hole_depth]) cylinder(d = boss_hole_d, h = boss_hole_depth + 1);
    }

    standoffs(pico_pos, pico_holes, pico_standoff_h);
    standoffs(iface_pos, iface_holes, iface_standoff_h);
}

// Modelled lid-on-box: plate at z 0..lid_t, lip hanging below.
module lid() {
    difference() {
        union() {
            translate([-wall, -wall, 0])
                rbox([inner.x + 2 * wall, inner.y + 2 * wall, lid_t], corner_r + wall);
            translate([0, 0, -lip_h]) difference() {
                translate([fit, fit, 0]) rbox([inner.x - 2 * fit, inner.y - 2 * fit, lip_h], corner_r - fit);
                translate([fit + lip_w, fit + lip_w, -1])
                    rbox([inner.x - 2 * (fit + lip_w), inner.y - 2 * (fit + lip_w), lip_h + 2],
                         corner_r - fit - lip_w);
            }
        }
        for (c = boss_centers) {
            translate([0, 0, -lip_h - 1]) boss_shape(c, fit, lip_h + 1);
            translate([c.x, c.y, -lip_h - 1]) cylinder(d = lid_screw_d, h = lid_t + lip_h + 2);
        }
    }
}

// Print the box open side up and the lid plate side down.
if (part == "box" || part == "both") box();
if (part == "lid" || part == "both")
    translate([0, part == "both" ? -ear_len - 3 * wall - 10 : inner.y, lid_t]) rotate([180, 0, 0]) lid();
