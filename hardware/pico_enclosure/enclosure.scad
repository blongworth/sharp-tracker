// SHARP Tracker: 3D-printable enclosure for the Pico W NMEA collector.
//
// Box + screw-down lid. One perfboard carrier on standoffs holds the opto-isolated
// NMEA 0183 input (see README.md) and a socket the Pico W plugs into. Ports:
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
// "pico" and "carrier" are reference models of the Pico W and the perfboard carrier
// with the opto-isolated NMEA input (not for printing); "assembly" shows them in the box.

part = "both"; // [both, box, lid, pico, carrier, assembly]

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
pico_row_y = (21 - 17.78) / 2;   // pin rows are 17.78 mm (0.7") apart
pico_headers = true;     // male pin headers on the Pico (Pico WH, or solder your own)
header_spacer = 2.5;     // plastic spacer of the Pico's male header

/* [Carrier board] */
carrier_size = [70, 50]; // 5 x 7 cm perfboard: opto input + Pico socket
carrier_holes = [[3, 3], [3, 47], [67, 3], [67, 47]];   // MEASURE: match your perfboard
carrier_pos = [30, 6];   // Pico USB end faces the back wall (x = 0)
carrier_standoff_h = 5;
standoff_d = 5;
standoff_hole_d = 1.8;   // M2 self-tapping
socket_h = 8.5;          // body height of the 1 x 20 female socket strips

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

    standoffs(carrier_pos, carrier_holes, carrier_standoff_h);
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

// ---------------------------------------------------------------- reference models
// Not printed: they show the boards on their standoffs so heights and clearances
// can be checked. Origin is the board's lower-left corner, PCB bottom at z = 0.

pcb_t = 1.6;

// Raspberry Pi Pico W (51 x 21 mm). Micro-USB on the x = 0 end, antenna at x = 51.
module pico_w() {
    t = 1;
    pin_x = [for (i = [0 : 19]) 1.37 + i * 2.54];
    color("#1d7a3a") difference() {
        cube([pico_size.x, pico_size.y, t]);
        for (h = pico_holes) translate([h.x, h.y, -1]) cylinder(d = 2.1, h = t + 2);
        for (x = pin_x, y = [0, pico_size.y]) translate([x, y, -1]) cylinder(d = 1, h = t + 2, $fn = 12);
    }
    // castellated pads
    color("gold") for (x = pin_x, y = [0, pico_size.y - 1.6])
        translate([x - 0.8, y, t]) cube([1.6, 1.6, 0.05]);
    translate([0, 0, t]) {
        color("silver") translate([-1.3, pico_size.y / 2 - 4, 0]) cube([5.7, 8, 2.9]);   // micro-USB
        color("#222") translate([21, 7, 0]) cube([7, 7, 0.9]);                           // RP2040
        color("#222") translate([14, 3.5, 0]) cube([5.3, 5, 0.8]);                       // flash
        color("silver") translate([33.5, 5, 0]) cube([10.5, 11, 1.3]);                  // CYW43439 shield can
        color("#ddd") translate([10, 13.5, 0]) cube([3.5, 4.5, 1.4]);                    // BOOTSEL
        color("#ddd") translate([11.75, 15.75, 1.4]) cylinder(d = 2.2, h = 0.6);
        color("#eee") translate([4.5, 16, 0]) cube([1.6, 0.8, 0.5]);                     // LED
    }
    if (pico_headers) for (x = pin_x, y = [pico_row_y, pico_size.y - pico_row_y]) {
        color("#222") translate([x - 1.27, y - 1.27, -header_spacer]) cube([2.54, 2.54, header_spacer]);
        color("gold") translate([x - 0.32, y - 0.32, -header_spacer - 6]) cube([0.64, 0.64, header_spacer + 6 + t + 0.5]);
    }
}

// Perfboard carrier (README "GPS input"). 0.1" grid, hole (i, j); x runs back -> front.
// The Pico plugs into two 1 x 20 socket strips on rows j = 9 and 16, USB end at i = 0,
// so Pico pin 2 (GP1) is hole (1, 9), pin 3 (GND) is (2, 9), pin 36 (3V3) is (4, 16).
// The opto input sits on rows j = 0..7: its isolated GPS side (J2, R1, D1, U1 pins 1-2)
// on rows 0-2 at the front end, its Pico side (U1 pins 4-6, R2, C1) on rows 5-7.
// The bare board under U1 between rows 2 and 5 is the isolation gap.
grid_n = [24, 17];
grid0 = [(carrier_size.x - (grid_n.x - 1) * 2.54) / 2, (carrier_size.y - (grid_n.y - 1) * 2.54) / 2];
function g(i, j) = [grid0.x + i * 2.54, grid0.y + j * 2.54];

pico_row_j = 9;
// Pico origin on the carrier: pin 1 (pin_x[0], pico_row_y) over hole (0, pico_row_j).
pico_on_carrier = [g(0, 0).x - 1.37, g(0, pico_row_j).y - pico_row_y,
                   pcb_t + socket_h + header_spacer];

module axial(a, b, d, len, body_col) {   // lying along x between holes a and b
    mid = (a + b) / 2;
    h = d / 2 + 0.6;
    color("silver") {
        for (p = [a, b]) translate([p.x, p.y, -1]) cylinder(d = 0.5, h = h + 1, $fn = 8);
        translate([a.x, a.y, h]) rotate([0, 90, 0]) cylinder(d = 0.5, h = b.x - a.x, $fn = 8);
    }
    color(body_col) translate([mid.x - len / 2, mid.y, h]) rotate([0, 90, 0]) cylinder(d = d, h = len, $fn = 24);
}

module dip6(p) {   // H11L1 in a socket, pin 1 at hole p, pins 1-3 along +x, 6-4 one row 7.62 mm up in y
    l = 2 * 2.54 + 3.8;
    translate([p.x - 1.9, p.y - 1.27, 0]) {
        color("#222") cube([l, 7.62 + 2.54, 3]);
        color("#333") translate([0, 1.27 + 0.6, 3]) cube([l, 7.62 - 1.2, 3.3]);
        color("#888") translate([1.2, 1.27 + 1.5, 6.3]) cylinder(d = 1, h = 0.05);   // pin 1 dot
    }
}

module socket_strip(p, n) {   // 1 x n round-pin female socket running along x from hole p
    color("#222") difference() {
        translate([p.x - 1.27, p.y - 1.27, 0]) cube([n * 2.54, 2.54, socket_h]);
        for (k = [0 : n - 1]) translate([p.x + k * 2.54, p.y, socket_h - 3]) cylinder(d = 1.2, h = 4, $fn = 12);
    }
}

module jst_xh(p, n) {   // n-way JST-XH top-entry header running along y from hole p
    w = (n - 1) * 2.5 + 4.9; d = 5.75; h = 7;
    translate([p.x - d / 2, p.y - 2.45, 0]) difference() {
        color("#eee") cube([d, w, h]);
        translate([0.8, 0.8, 1.5]) cube([d - 1.6, w - 1.6, h]);
    }
    color("gold") for (k = [0 : n - 1]) translate([p.x - 0.32, p.y + k * 2.5 - 0.32, -3]) cube([0.64, 0.64, 9]);
}

module carrier_board() {
    color("#c79a5b") difference() {
        cube([carrier_size.x, carrier_size.y, pcb_t]);
        for (h = carrier_holes) translate([h.x, h.y, -1]) cylinder(d = 3.2, h = pcb_t + 2);
        for (i = [0 : grid_n.x - 1], j = [0 : grid_n.y - 1])
            translate([each g(i, j), -1]) cylinder(d = 1, h = pcb_t + 2, $fn = 8);
    }
    translate([0, 0, pcb_t]) {
        // Pico side of the opto input
        axial(g(5, 6), g(9, 6), 2.4, 6.3, "#d9c08c");         // R2 4.7k pull-up, U1 pin 4 to 3V3
        translate([each g(10.5, 7), 0]) color("#d8b23a")      // C1 100 nF at U1 pin 6
            translate([-2, -1.25, 0]) cube([4, 2.5, 5]);
        dip6(g(12, 2));                                       // U1 H11L1: pins 1-3 row 2, 6-4 row 5
        // isolated GPS side
        axial(g(16, 2), g(20, 2), 1.8, 3.6, "#e07a2a");       // D1 1N4148 across the LED
        axial(g(13, 0), g(21, 0), 3.2, 9, "#7aa6c9");         // R1 390R 1/2 W
        jst_xh(g(22, 1), 2);                                  // J2 GPS in from DB9 pins 2 + 5
        // Pico socket
        for (j = [pico_row_j, pico_row_j + 7]) socket_strip(g(0, j), 20);
    }
}

// Print the box open side up and the lid plate side down.
if (part == "box" || part == "both") box();
if (part == "pico") pico_w();
if (part == "carrier") {
    carrier_board();
    translate(pico_on_carrier) pico_w();
}
if (part == "assembly") {
    box();
    translate([carrier_pos.x, carrier_pos.y, carrier_standoff_h]) {
        carrier_board();
        translate(pico_on_carrier) pico_w();
    }
    color("white", 0.25) translate([0, 0, inner.z]) lid();
}
if (part == "lid" || part == "both")
    translate([0, part == "both" ? -ear_len - 3 * wall - 10 : inner.y, lid_t]) rotate([180, 0, 0]) lid();
