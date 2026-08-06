#!/usr/bin/env python
"""Dump every live NW4R text pane, with an optional screenshot.

This is the first thing to run when adding support for a new screen: it shows
what text the screen exposes and, crucially, the pane names to ask for.

    python tools/panes_dump.py                 # list panes
    python tools/panes_dump.py --shot out.png  # and capture the frame
    python tools/panes_dump.py --watch 20      # re-dump on change for 20s

Remember that panes keep their last string after their screen closes, so a
listing always contains stale entries from earlier screens. Compare two dumps,
or drive the screen and watch what changes.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from rhfaccess.dolphin import DolphinLink  # noqa: E402
from rhfaccess.games import panes as panes_mod  # noqa: E402
from rhfaccess.games import rhf  # noqa: E402


def snapshot(link, index):
    found = index.scan()
    if found is None:
        print("MEM2 unreadable — see tools/diag.py. Nothing to dump.")
        return {}
    out = {}
    for name in sorted(found):
        text = index.text(name)
        if text:
            out[name] = (found[name], text)
    return out


def show(rows, only=None):
    for name, (addr, text) in rows.items():
        if only and name not in only:
            continue
        print(f"  0x{addr:08X}  {name:<22} {text[:72]!r}")


def main() -> int:
    argv = sys.argv[1:]
    shot = None
    if "--shot" in argv:
        i = argv.index("--shot")
        shot = argv[i + 1]
        del argv[i:i + 2]
    watch = 0.0
    if "--watch" in argv:
        i = argv.index("--watch")
        watch = float(argv[i + 1])
        del argv[i:i + 2]

    link = DolphinLink()
    if not link.ensure_connected():
        print("Could not hook Dolphin.")
        return 1

    state = link.u32(rhf.ADDR_MENU_STATE)
    print(f"menu state 0x{rhf.ADDR_MENU_STATE:08X} = {state}   "
          f"grid idx = {link.u8(rhf.ADDR_GRID_INDEX)}")

    index = panes_mod.PaneIndex(link)
    rows = snapshot(link, index)
    print(f"\n{len(rows)} live text panes:\n")
    show(rows)

    if shot:
        import pad
        hwnd = pad.find_dolphin()
        if hwnd and pad.screenshot(hwnd, shot):
            print(f"\nscreenshot -> {shot}")

    if watch:
        print(f"\nwatching {watch:.0f}s for changes...")
        deadline = time.monotonic() + watch
        previous = {k: v[1] for k, v in rows.items()}
        while time.monotonic() < deadline:
            time.sleep(1.0)
            current = snapshot(link, index)
            now_state = link.u32(rhf.ADDR_MENU_STATE)
            changed = {k: v for k, v in current.items()
                       if previous.get(k) != v[1]}
            if changed:
                print(f"\n[state {now_state}] changed:")
                show(changed)
                previous.update({k: v[1] for k, v in changed.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
