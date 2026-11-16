# Bill of materials: Pico W collector

Parts for one field unit: Pico W, opto-isolated NMEA 0183 input, 3D-printed
enclosure, USB wall power. Build steps and the input schematic are in
[README.md](README.md).

Prices are rough single-quantity USD estimates (2026) to give a budget, not quotes.
Use stainless (A2/A4) screws on a ship.

## Electronics

| Ref | Qty | Part | Spec / notes | ~USD |
| --- | --- | --- | --- | --- |
| — | 1 | Raspberry Pi Pico WH (or Pico 2 WH) | WiFi required. The "H" version has male headers pre-soldered; otherwise buy a Pico W plus 2 × 1×20 male 2.54 mm headers | 7 |
| — | 2 | 1×20 round-pin (machined) female socket strip, 2.54 mm | Pico socket on the carrier; round-pin grips better under vibration than leaf-spring | 2 |
| U1 | 1 | H11L1 optocoupler, DIP-6 | Schmitt-trigger, open-collector output (onsemi H11L1M, Everlight H11L1, Isocom H11L1) | 1 |
| — | 1 | DIP-6 IC socket | so U1 can be swapped | 0.30 |
| R1 | 1 | Resistor 390 Ω, ½ W | LED current limit. 270 Ω if a weak RS-422 talker gives garbled data | 0.10 |
| R2 | 1 | Resistor 4.7 kΩ, ¼ W | output pull-up | 0.10 |
| D1 | 1 | 1N4148 diode | reverse protection across the LED | 0.10 |
| C1 | 1 | Capacitor 100 nF ceramic | U1 VCC decoupling | 0.10 |
| J2 | 1 | JST-XH 2-way header, top entry (B2B-XH-A) + XHP-2 housing + 2 crimped leads | GPS pair from the DB9. Pre-crimped leads avoid needing a JST crimper | 1 |
| — | 1 | Perfboard, 5 × 7 cm | carrier for U1, parts and the Pico socket. Set `carrier_holes` in `enclosure.scad` to its mounting holes | 0.70 |
| — | — | Hookup wire, 24–26 AWG stranded, silicone or PTFE insulation | twisted GPS pair, DB9 → J2, about 15 cm | — |
| — | — | Bare tinned wire or 26 AWG solid | underside links to the socket pins | — |
| — | — | Heat-shrink, small, and a few small zip ties | DB9 solder cups; strain relief | — |

## Connectors and power

| Qty | Part | Spec / notes | ~USD |
| --- | --- | --- | --- |
| 1 | DE-9 (DB9) **male** panel connector, solder cup | GPS input; pin 2 = A(+)/TxD, pin 5 = B(−)/signal GND | 2 |
| 1 | DB9 jackscrew set (4-40 female screwlocks + nuts) | holds the DB9 in the wall and takes the GPS cable's screws | 1 |
| 1 | Panel-mount USB-C female → micro-USB male cable, 20–30 cm | **must carry data**, not charge-only, so firmware/console work with the lid closed. Measure its flange and set `usb_screw_dx` / `usb_cut` before printing | 8 |
| 2 | Screws + nuts for the USB-C flange | usually M3 × 8; match your cable | 0.20 |
| 1 | USB wall adapter, 5 V ≥ 1 A, UL-listed | 120 VAC; Pico W draws about 100 mA with WiFi | 8 |
| 1 | USB-C cable, adapter → enclosure | length to suit the mounting spot | 5 |

## Enclosure

| Qty | Part | Spec / notes | ~USD |
| --- | --- | --- | --- |
| 1 | Printed box (`enclosure.scad`, `part = "box"`) | PETG or ASA (PLA softens in a hot wheelhouse). About 60–75 g | 2 |
| 1 | Printed lid (`part = "lid"`) | About 20–30 g | 1 |
| 4 | M3 × 12 self-tapping screws for plastic, pan head, stainless | lid, threaded straight into the printed bosses (2.6 mm pilot) | 0.50 |
| 4 | M2 × 5 self-tapping screws | carrier board standoffs | 0.25 |
| 4 | #8 or M4 screws, stainless | mounting ears to bulkhead/bench; or use VHB tape | 0.50 |

## Ship-side cabling (depends on the GPS)

| Qty | Part | When |
| --- | --- | --- |
| 1 | DB9 female screw-terminal adapter | bare-wire NMEA 0183 (RS-422): A → pin 2, B → pin 5 |
| — | 2-conductor shielded twisted pair, length as needed | run from the GPS talker to the box. Ground the shield at the talker end only |
| 1 | DB9 cable or gender changer | GPS with an RS-232 DB9 port; the box end needs a female DB9 |

## Bench test (optional, reusable)

| Qty | Part | Use |
| --- | --- | --- |
| 1 | USB to RS-232 adapter (DB9) | drive the box from `scripts/simulate_nmea_source.py --serial` |
| 1 | Multimeter | bench check of the input circuit (README) |
| 1 | Soldering iron | perfboard and DB9 |
| 1 | JST crimper (e.g. Engineer PA-09) | only if crimping your own J2 leads |

## Total

About **$40 per unit** for the electronics, connectors, power, and printed
enclosure. Ship-side cabling and bench tools are extra.
