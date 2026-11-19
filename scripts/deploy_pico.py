"""Install the Pico firmware onto a CIRCUITPY drive.

Assumes CircuitPython is already flashed (README "Pico W firmware"). Installs the
libraries in firmware/pico_nmea_tracker/requirements.txt with circup, copies the
firmware files, and creates settings.toml from the example only if the board
has none, so your WiFi credentials and token are never overwritten.

    uv run python scripts/deploy_pico.py                  # auto-detect the drive
    uv run python scripts/deploy_pico.py --path /Volumes/CIRCUITPY
    uv run python scripts/deploy_pico.py --skip-libs      # firmware files only (offline)
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = ROOT / "firmware" / "pico_nmea_tracker"
REQUIREMENTS = FIRMWARE / "requirements.txt"
SETTINGS_EXAMPLE = FIRMWARE / "settings.toml.example"

# (source, name on the board). code.py goes last: CircuitPython auto-reloads on
# every write, and the modules code.py imports should already be in place.
FILES = [
    (FIRMWARE / "nmea_min.py", "nmea_min.py"),
    (FIRMWARE / "fixes.py", "fixes.py"),
    (FIRMWARE / "status.py", "status.py"),
    (ROOT / "sharp_tracker" / "averaging.py", "averaging.py"),
    (FIRMWARE / "code.py", "code.py"),
]


def find_drive() -> Path | None:
    candidates = [
        Path("/Volumes/CIRCUITPY"),
        Path("/media") / getpass.getuser() / "CIRCUITPY",
        Path("/run/media") / getpass.getuser() / "CIRCUITPY",
    ]
    return next((p for p in candidates if (p / "boot_out.txt").exists()), None)


def circup_command() -> list[str]:
    # The console script lives next to the interpreter in the uv venv.
    local = Path(sys.executable).parent / "circup"
    if local.exists():
        return [str(local)]
    found = shutil.which("circup")
    if found:
        return [found]
    sys.exit("circup not found; run this with `uv run` (it is a dev dependency)")


def install_libs(drive: Path) -> None:
    cmd = [*circup_command(), "--path", str(drive), "install", "-r", str(REQUIREMENTS)]
    print("$", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def flush_to_board() -> None:
    """Push the OS write cache to the board now (macOS writes FAT drives lazily)."""
    if hasattr(os, "sync"):
        os.sync()


def copy_if_changed(src: Path, dest: Path) -> bool:
    data = src.read_bytes()
    if dest.exists() and dest.read_bytes() == data:
        return False
    dest.write_bytes(data)  # plain write: FAT has no useful metadata to copy
    # CircuitPython restarts code.py on every write and reads the flash directly,
    # so each module must be on the board before the code.py write restarts it.
    flush_to_board()
    return True


def deploy(drive: Path, skip_libs: bool = False) -> None:
    if not skip_libs:
        install_libs(drive)
    for src, name in FILES:
        changed = copy_if_changed(src, drive / name)
        print(f"{'copied ' if changed else 'same   '} {name}")
    settings = drive / "settings.toml"
    if settings.exists():
        print("kept    settings.toml")
    else:
        shutil.copyfile(SETTINGS_EXAMPLE, settings)
        print(f"created settings.toml from the example: edit {settings} before use")
    flush_to_board()
    # A path lookup can succeed from the OS cache while the directory entry
    # never reached the board; the listing is what the board will see.
    listed = set(os.listdir(drive))
    missing = [name for _, name in FILES if name not in listed]
    if missing:
        sys.exit(
            f"Not on the board after copying: {', '.join(missing)}. Eject the drive, "
            "replug the Pico, and run this again."
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", type=Path, help="CIRCUITPY mount point (default: auto-detect)")
    parser.add_argument("--skip-libs", action="store_true", help="don't run circup")
    args = parser.parse_args()

    drive = args.path or find_drive()
    if drive is None or not (drive / "boot_out.txt").exists():
        sys.exit(
            f"No CIRCUITPY drive found{f' at {drive}' if drive else ''}. "
            "Plug in the Pico (flash CircuitPython first) or pass --path."
        )
    deploy(drive, skip_libs=args.skip_libs)


if __name__ == "__main__":
    main()
