#!/usr/bin/env python
"""Walk the game-select menu, logging what we would say next to a screenshot.

The companion currently names entries with a fixed offset into the text
archive, and that offset is wrong past the first row. Fixing it needs ground
truth, and the only source of ground truth for what is actually on screen is
the screen. So: step the cursor, and at every stop record

    cursor index | the name we would speak | a PNG of the frame

Reading the PNGs back gives the real index -> name table, which is both the
answer and the yardstick for any memory field that claims to encode it.

    python tools/sweep.py                 # walk the current row both ways
    python tools/sweep.py --keys S,S,S,D  # or drive an explicit key sequence

Output goes to tools/sweep_out/ by default: shot_NN.png plus sweep.tsv.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import pad  # noqa: E402

from rhfaccess.dolphin import DolphinLink  # noqa: E402
from rhfaccess.games import archive as archive_mod  # noqa: E402
from rhfaccess.games import rhf  # noqa: E402

# The name banner fades in only once the selection settles, so a short wait
# captures an empty strip mid-animation.
SETTLE = 1.3

# The game prints the highlighted entry's name across the bottom of the screen,
# so each frame carries its own ground truth. Cropping to that banner keeps the
# captures small.
NAME_BANNER = (0.30, 0.79, 0.95, 1.0)


def spoken_name(link, archive, index):
    """Exactly what GridCursorProbe would say, or None if it would stay quiet."""
    if index is None or index == rhf.INVALID_INDEX or archive is None:
        return None
    name_index = rhf.archive_index_for(index)
    if name_index is None:
        return None
    label = archive.text(name_index)
    if not label:
        return None
    label = label.strip()
    if len(label) > rhf.MAX_NAME_LENGTH or "\n" in label or not label.isprintable():
        return None
    return label


def main() -> int:
    argv = sys.argv[1:]
    out_dir = HERE / "sweep_out"
    if "--out" in argv:
        i = argv.index("--out")
        out_dir = Path(argv[i + 1])
        del argv[i:i + 2]

    keys = None
    if "--keys" in argv:
        i = argv.index("--keys")
        keys = [k.strip().upper() for k in argv[i + 1].split(",") if k.strip()]
        del argv[i:i + 2]

    out_dir.mkdir(parents=True, exist_ok=True)

    link = DolphinLink()
    if not link.ensure_connected():
        print("Could not hook Dolphin. Is it running with the game loaded?")
        return 1

    hwnd = pad.grab()
    if hwnd is None:
        print("Could not find the Dolphin window.")
        return 1
    time.sleep(0.4)

    print("Locating text archive...")
    archive = archive_mod.find(link)
    if archive is None:
        print("No text archive found — names will be blank.")
    else:
        print(f"archive 0x{archive.magic_addr:08X}, {archive.count} records")

    rows = []
    shot = 0

    def capture(step_label):
        nonlocal shot
        index = link.u8(rhf.ADDR_GRID_INDEX)
        name = spoken_name(link, archive, index)
        path = out_dir / f"shot_{shot:02d}.png"
        ok = pad.screenshot(hwnd, str(path), crop=NAME_BANNER)
        rows.append((shot, step_label, index, name or "", path.name if ok else "FAILED"))
        print(f"  {shot:02d} {step_label:<10} idx={index} says={name!r}")
        shot += 1

    if keys is None:
        # Default: walk to one end, then sweep all the way back.
        capture("start")
        for i in range(12):
            before = link.u8(rhf.ADDR_GRID_INDEX)
            pad.tap("S")
            time.sleep(SETTLE)
            if link.u8(rhf.ADDR_GRID_INDEX) == before:
                break            # hit the end of the row; no wrap
            capture(f"down{i + 1}")
        for i in range(12):
            before = link.u8(rhf.ADDR_GRID_INDEX)
            pad.tap("W")
            time.sleep(SETTLE)
            if link.u8(rhf.ADDR_GRID_INDEX) == before:
                break
            capture(f"up{i + 1}")
    else:
        capture("start")
        for i, key in enumerate(keys):
            pad.tap(key)
            time.sleep(SETTLE)
            capture(f"{key}{i + 1}")

    log = out_dir / "sweep.tsv"
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("shot\tstep\tindex\tspoken\timage\n")
        for row in rows:
            fh.write("\t".join(str(c) for c in row) + "\n")
    print(f"\n{len(rows)} captures -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
