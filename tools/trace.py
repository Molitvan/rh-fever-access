#!/usr/bin/env python
"""Read-only trace of the state the screen probes gate on. Sends no input.

    python tools/trace.py            # wait for a boot, then trace 5 minutes
    python tools/trace.py 600        # trace for 10 minutes instead
    python tools/trace.py 300 my.log # and choose where the log goes

Start this *before* booting the game. It waits for Dolphin to have a disc
loaded and then records, with timestamps:

  * ADDR_GRID_INDEX and its mirror, whenever either changes
  * a pane sweep every 1.5s: how many panes are live, which appeared or
    vanished, and the text of the panes the probes ask for

Every screen probe in rhf.py decides where you are from ADDR_GRID_INDEX, and
two of them switch themselves off for good the first time it reads anything
but 0xFF. This exists to find out what that byte actually reads on the screens
that stay silent — the title screen, the file select, and the title screen
reached a second time by backing out of the menu — rather than assuming.

Console output is only the changes; the log gets everything.
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

SWEEP_INTERVAL = 1.5
POLL_INTERVAL = 0.1

# The panes the probes ask for, so the log shows whether each was present and
# what it held at the moment its probe would have been reading it.
WATCHED = (
    (rhf.PANE_FILE_PROMPT,)
    + tuple(rhf.PANE_FILE_FLOW.format(i) for i in range(rhf.FILE_SLOT_COUNT))
    + tuple(rhf.PANE_FILE_MEDALS.format(i) for i in range(rhf.FILE_SLOT_COUNT))
    + (rhf.PANE_CARD_TITLE, rhf.PANE_CARD_TEXT, rhf.PANE_CARD_CONTROLS,
       rhf.PANE_TUTORIAL, rhf.PANE_REWARD,
       rhf.PANE_NOTICE_TITLE, rhf.PANE_NOTICE_BODY, rhf.PANE_NOTICE_PROMPT,
       rhf.PANE_RESULT_CAPTION, rhf.PANE_PERFECT)
)


def _hex(value, width: int = 2) -> str:
    return "None" if value is None else f"0x{value:0{width}X}"


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    logpath = sys.argv[2] if len(sys.argv) > 2 else str(HERE / "trace.log")
    log = open(logpath, "w", encoding="utf-8")

    def emit(line: str) -> None:
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()   # flushed per line so a killed run still leaves a log

    link = DolphinLink()
    print("Waiting for a game to boot... (Ctrl+C to quit)", flush=True)
    while not link.ensure_connected():
        time.sleep(0.5)
    emit(f"hooked: {link.game_id()} {link.game_title()!r}")
    emit(f"READY - tracing for {seconds:.0f}s. Log: {logpath}")

    index = panes_mod.PaneIndex(link, rescan_interval=0.0)
    start = time.monotonic()
    last_pair = object()      # not None: None is a legitimate reading
    last_names = None
    next_sweep = 0.0

    while time.monotonic() - start < seconds:
        now = time.monotonic() - start
        idx = link.u8(rhf.ADDR_GRID_INDEX)
        mirror = link.u8(rhf.ADDR_GRID_INDEX + 1)
        entry = link.u32(rhf.ADDR_GRID_ENTRY_PTR)
        slot = link.u8(rhf.ADDR_FILE_SLOT)

        pair = (idx, mirror, slot)
        if pair != last_pair:
            last_pair = pair
            emit(f"[{now:7.2f}] idx={_hex(idx)} mirror={_hex(mirror)} "
                 f"slot={_hex(slot)} entry_ptr={_hex(entry, 8)}")

        if now >= next_sweep:
            next_sweep = now + SWEEP_INTERVAL
            found = index.scan()
            if found is None:
                # Not "no panes" — the sweep could not read MEM2. Logging that
                # as an empty screen is precisely the confusion this trace
                # exists to catch, so it gets its own line.
                emit(f"[{now:7.2f}] PANES unreadable (MEM2 sweep failed)")
            else:
                names = frozenset(found)
                if names != last_names:
                    added = sorted(names - (last_names or frozenset()))
                    gone = sorted((last_names or frozenset()) - names)
                    last_names = names
                    emit(f"[{now:7.2f}] PANES {len(names)} live  "
                         f"+{len(added)} -{len(gone)}   idx={_hex(idx)}")
                    if added:
                        emit(f"           appeared: {' '.join(added[:30])}")
                    if gone:
                        emit(f"           vanished: {' '.join(gone[:30])}")
                texts = [f"{name}={text[:44]!r}"
                         for name in WATCHED
                         if name in found and (text := index.text(name))]
                if texts:
                    emit("           " + "  ".join(texts))

        time.sleep(POLL_INTERVAL)

    emit("done")
    log.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
