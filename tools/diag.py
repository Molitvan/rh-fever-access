#!/usr/bin/env python
"""Health check: can the companion actually see the game?

Run this first when the companion is connected but wrong or silent.

    uv run python tools/diag.py

Being hooked and being able to read are different things, and the difference is
invisible by ear. MEM1 reaches Dolphin through dolphin-memory-engine, so the
disc ID, the cursor index and the entry pointer keep working no matter what
happens to the raw MEM2 backend. MEM2 is where the text panes and the name
archive live. Lose it and the companion still announces the game, still follows
the cursor over the hardcoded extras, and goes silent on everything else — which
is not a shape of failure anyone can diagnose from the outside.

Every line below prints OK or a reason, and the exit status is non-zero if
anything is wrong.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from rhfaccess.dolphin import DolphinLink, MEM2_START  # noqa: E402
from rhfaccess.games import archive, panes, rhf  # noqa: E402
from rhfaccess.rawmem import RawMemory, find_processes  # noqa: E402

problems: list = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'OK ' if ok else 'BAD'}] {label}{': ' + detail if detail else ''}")
    if not ok:
        problems.append(label)
    return ok


def _dump_candidate_regions() -> None:
    """List the mapped regions that look like they could be emulated RAM.

    rawmem.py identifies MEM1 by an exact region size and a disc ID at its
    start. If Dolphin ever lays its arena out differently, the attach simply
    fails and nothing says why — so show the sizes on offer next to the ones
    being looked for.
    """
    from rhfaccess import rawmem

    print("\n     mapped regions holding something that looks like a disc ID:")
    for pid in find_processes():
        probe = RawMemory()
        handle = rawmem.k32.OpenProcess(
            rawmem.PROCESS_QUERY_INFORMATION | rawmem.PROCESS_VM_READ, False, pid)
        if not handle:
            print(f"       pid {pid}: cannot open process")
            continue
        probe._handle = handle
        try:
            for base, size, mtype in probe._regions():
                if mtype != rawmem.MEM_MAPPED or size < 0x1000000:
                    continue
                head = probe._read_raw(base, 6)
                if head and head.isalnum():
                    print(f"       pid {pid}: 0x{base:X}  {size:#x} "
                          f"({size / (1 << 20):.0f} MiB)  {head!r}")
        finally:
            probe.close()
    print(f"     rawmem.py accepts MEM1 sizes {[hex(s) for s in rawmem.MEM1_SIZES]} "
          f"and MEM2 sizes {[hex(s) for s in rawmem.MEM2_SIZES]}")


def main() -> int:
    print("Dolphin processes")
    pids = find_processes()
    if not check("Dolphin is running", bool(pids), f"pids {pids}" if pids else "none found"):
        return 1
    if len(pids) > 1:
        print("     note: more than one Dolphin. Only the one with a game loaded is used.")

    print("\nRaw MEM2 backend")
    raw = RawMemory()
    if not check("attached to a Dolphin with a game", raw.attach()):
        print("     Nothing here has a game running, or the arena layout is one"
              " rawmem.py does not recognise.")
        # The layout is a whitelist of region sizes, so a Dolphin that arranges
        # its arena differently fails to attach with no other symptom. Print
        # what is actually there, so the next session can widen the whitelist
        # instead of re-deriving the problem.
        _dump_candidate_regions()
        return 1
    check("disc ID readable", raw.game_id is not None, str(raw.game_id))
    print(f"       mem1 0x{raw.mem1_base:X} ({raw.mem1_size // (1 << 20)} MiB)  "
          f"mem2 0x{raw.mem2_base:X} ({raw.mem2_size // (1 << 20)} MiB)")
    check("backend healthy", raw.healthy())
    raw.close()

    print("\nLink")
    link = DolphinLink()
    check("connected", link.ensure_connected())
    game_id = link.game_id()
    check("game is Rhythm Heaven Fever", rhf.is_rhythm_heaven(game_id), str(game_id))
    check("MEM1 readable", link.u8(rhf.ADDR_GRID_INDEX) is not None)
    check("MEM2 readable", link.mem2_available, f"0x{MEM2_START:08X}")

    print("\nGame state (MEM1)")
    index = link.u8(rhf.ADDR_GRID_INDEX)
    committed = link.u8(rhf.ADDR_GRID_INDEX + 1)
    entry = link.pointer(rhf.ADDR_GRID_ENTRY_PTR)
    print(f"       grid index {index}   committed {committed}   "
          f"entry ptr {entry and hex(entry)}")
    print(f"       no_selection() = {rhf.no_selection(link)}"
          "   (True means the title-screen probe is allowed to look)")

    print("\nPane sweep (MEM2)")
    started = time.monotonic()
    found = panes.PaneIndex(link).scan()
    took = time.monotonic() - started
    if found is None:
        check("sweep completed", False, f"MEM2 unreadable, {took:.2f}s")
    else:
        check("sweep completed", True, f"{len(found)} live text panes in {took:.2f}s")
        for name in sorted(found)[:12]:
            print(f"       {name}")
        if len(found) > 12:
            print(f"       ... and {len(found) - 12} more")

    print("\nText archive (MEM2)")
    started = time.monotonic()
    found_archive = archive.find(link)
    took = time.monotonic() - started
    check("archive located", found_archive is not None, f"{took:.2f}s")
    if found_archive is not None:
        print(f"       {found_archive.count} entries, "
              f"short_ratio {found_archive.short_ratio:.2f}")
        for cursor in (0, 5, 9):
            print(f"       cursor {cursor} -> {rhf.label_for(cursor, found_archive)!r}")
    else:
        print("     Without this the game names on the tower cannot be read. The"
              " hardcoded extras (Rhythm Cafe, Rhythm Toys, Endless Games) will"
              " still speak, which makes this look like a menu bug rather than a"
              " memory one.")

    print()
    if problems:
        print(f"{len(problems)} problem(s): " + "; ".join(problems))
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
