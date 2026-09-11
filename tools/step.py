#!/usr/bin/env python
"""One-step-per-invocation wrapper around the scanner.

`scan.py` is an interactive REPL. That does not work when the person moving the
cursor and the person running the filter are taking turns, so this stores the
scan state on disk and applies exactly one operation per run.

    uv run python tools/step.py new            # take the baseline
    (player moves the cursor)
    uv run python tools/step.py changed        # keep only what moved
    (player sits still)
    uv run python tools/step.py unchanged      # kill the animation/timer churn
    uv run python tools/step.py list

State lives next to the repo in .scanstate.pkl unless --state says otherwise.
Read-only with respect to the game: this never writes emulated memory.
"""

from __future__ import annotations

import pickle
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from scan import TYPES, Scanner, dump, parse_number  # noqa: E402

from rhfaccess.dolphin import DolphinLink  # noqa: E402

DEFAULT_STATE = HERE.parent / ".scanstate.pkl"


def load_state(path: Path) -> Scanner:
    if path.exists():
        with open(path, "rb") as fh:
            return pickle.load(fh)
    return Scanner()


def save_state(path: Path, scanner: Scanner) -> None:
    with open(path, "wb") as fh:
        pickle.dump(scanner, fh, protocol=pickle.HIGHEST_PROTOCOL)


def summarise(scanner: Scanner) -> None:
    if scanner.candidates is None:
        state = "everything (no filter applied yet)" if scanner.started else "no baseline"
    else:
        state = f"{len(scanner.candidates):,} addresses"
    print(f"[state] type={scanner.kind}  candidates={state}")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    state_path = DEFAULT_STATE
    if "--state" in argv:
        i = argv.index("--state")
        state_path = Path(argv[i + 1])
        del argv[i:i + 2]
    include_mem2 = "--mem2" in argv
    if include_mem2:
        argv.remove("--mem2")

    # Idle passes need a controlled gap: the point of `unchanged` is to prove a
    # value held still over a real interval, not over a few microseconds.
    delay = 0.0
    if "--delay" in argv:
        i = argv.index("--delay")
        delay = float(argv[i + 1])
        del argv[i:i + 2]

    if not argv:
        print(__doc__)
        return 1

    cmd, args = argv[0].lower(), argv[1:]

    link = DolphinLink()
    if not link.ensure_connected():
        print("Could not hook Dolphin. Is it running with the game loaded?")
        return 1

    scanner = load_state(state_path)

    if delay > 0:
        print(f"[wait] {delay:.1f}s before sampling...")
        time.sleep(delay)

    if cmd == "new":
        scanner = Scanner(include_mem2=include_mem2)
        if args and args[0] in TYPES:
            scanner.kind = args[0]
        started = time.monotonic()
        scanner.reset()
        print(f"[timing] baseline in {time.monotonic() - started:.1f}s")
    elif cmd in ("changed", "unchanged", "inc", "dec"):
        started = time.monotonic()
        scanner.filter(cmd)
        print(f"[timing] {cmd} in {time.monotonic() - started:.1f}s")
    elif cmd in ("eq", "ne", "gt", "lt"):
        value = parse_number(args[0]) if args else None
        if value is None:
            print(f"Usage: step.py {cmd} <number>")
            return 1
        scanner.filter(cmd, value)
    elif cmd == "hold":
        samples = int(args[0]) if args and args[0].isdigit() else 8
        seconds = float(args[1]) if len(args) > 1 and _is_float(args[1]) else 2.0
        scanner.hold(samples, seconds)
    elif cmd == "snap":
        scanner.refresh()
    elif cmd == "list":
        count = int(args[0]) if args and args[0].isdigit() else 20
        scanner.list(count)
    elif cmd == "watch":
        addr = parse_number(args[0]) if args else None
        kind = args[1] if len(args) > 1 and args[1] in TYPES else scanner.kind
        seconds = float(args[-1]) if args and _is_float(args[-1]) and len(args) > 1 else 10.0
        if addr is None:
            print("Usage: step.py watch <addr> [type] [seconds]")
            return 1
        watch_for(link, int(addr), kind, seconds)
    elif cmd == "dump":
        addr = parse_number(args[0]) if args else None
        length = int(parse_number(args[1]) or 64) if len(args) > 1 else 64
        if addr is None:
            print("Usage: step.py dump <addr> [bytes]")
            return 1
        dump(link, int(addr), length)
    elif cmd == "auto":
        cycles = int(args[0]) if args and args[0].isdigit() else 12
        down = args[1].upper() if len(args) > 1 else "S"
        up = args[2].upper() if len(args) > 2 else "W"
        settle = float(args[3]) if len(args) > 3 and _is_float(args[3]) else 0.45
        auto_scan(scanner, cycles, down, up, settle, include_mem2)
    elif cmd == "drive":
        steps = int(args[0]) if args and args[0].isdigit() else 3
        down = args[1].upper() if len(args) > 1 else "S"
        up = args[2].upper() if len(args) > 2 else "W"
        drive(link, scanner, steps, down, up)
        return 0
    elif cmd == "track":
        seconds = float(args[0]) if args and _is_float(args[0]) else 12.0
        hz = float(args[1]) if len(args) > 1 and _is_float(args[1]) else 30.0
        track(link, scanner, seconds, hz)
        return 0
    elif cmd == "reset":
        if state_path.exists():
            state_path.unlink()
        print("Scan state cleared.")
        return 0
    else:
        print(f"Unknown command: {cmd}")
        return 1

    save_state(state_path, scanner)
    summarise(scanner)
    return 0


def _is_float(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False


def drive(link: DolphinLink, scanner: Scanner, steps: int, down: str, up: str) -> None:
    """Step the cursor down then back up, printing every candidate each time.

    This is the readable proof. A real index walks a staircase down and the
    identical staircase back up; anything else is coincidence that survived
    the filters.
    """
    import pad

    if scanner.candidates is None or scanner.candidates.size == 0:
        print("No candidates to drive against.")
        return
    if pad.grab() is None:
        print("Could not find the Dolphin window.")
        return

    addrs = [int(a) for a in scanner.candidates]
    reader = getattr(link, scanner.kind)

    def row(label: str) -> None:
        values = [reader(a) for a in addrs]
        cells = "  ".join(f"{'?' if v is None else v:>12}" for v in values)
        print(f"  {label:<12} {cells}")

    header = "  ".join(f"0x{a:08X}" for a in addrs)
    print(f"  {'':<12} {header}")
    row("origin")
    for i in range(steps):
        pad.tap(down)
        time.sleep(0.45)
        row(f"down x{i + 1}")
    for i in range(steps):
        pad.tap(up)
        time.sleep(0.45)
        row(f"up x{i + 1}")


def auto_scan(scanner: Scanner, cycles: int, down: str, up: str,
              settle: float, include_mem2: bool) -> None:
    """Drive the cursor and filter on a return-to-origin invariant.

    Each cycle: press Down, require the value to CHANGE from its origin value;
    press Up, require it to be IDENTICAL to that origin value again. The
    reference snapshot is taken once and never moves, so a value only survives
    if it tracks the cursor exactly, every single time.

    Direction-agnostic on purpose — it never assumes Down means "increment",
    which matters for a grid where the stride is a row width, or for a menu that
    stores an entry ID rather than a position.
    """
    import pad

    hwnd = pad.grab()
    if hwnd is None:
        print("Could not find the Dolphin window.")
        return
    print(f"Focused Dolphin (hwnd {hwnd}). Driving {down}/{up} for {cycles} cycles.")
    print("Hands off the keyboard until this finishes.")

    scanner.regions = list(Scanner(include_mem2=include_mem2).regions)
    origin = scanner.snapshot()
    scanner.baseline = origin
    scanner.candidates = None
    scanner.started = True
    time.sleep(settle)

    for i in range(1, cycles + 1):
        pad.tap(down)
        time.sleep(settle)
        scanner.filter("changed", baseline=origin, update_baseline=False, quiet=True)
        moved = scanner.candidate_count()

        pad.tap(up)
        time.sleep(settle)
        scanner.filter("unchanged", baseline=origin, update_baseline=False, quiet=True)
        back = scanner.candidate_count()

        print(f"  cycle {i:2}: changed-on-down {moved:,} -> back-at-origin {back:,}")
        if back == 0:
            print("  Nothing survived — the cursor may not have moved. Stopping.")
            return
        if back <= 4 and i >= 3:
            print("  Converged.")
            break

    scanner.baseline = origin


def track(link: DolphinLink, scanner: Scanner, seconds: float, hz: float) -> None:
    """Sample every surviving candidate while the player scrolls, then rank them.

    Filtering by value gets you to a few dozen addresses; it cannot tell a menu
    index apart from the sprite offsets that follow it. Behaviour can. An index
    steps by exactly one, stays inside a small range, and changes once per input
    — animation state does none of those things.
    """
    if scanner.candidates is None or scanner.candidates.size == 0:
        print("No candidate list to track. Filter first.")
        return

    addrs = [int(a) for a in scanner.candidates]
    reader = getattr(link, scanner.kind)
    series = {a: [] for a in addrs}

    print(f"Tracking {len(addrs)} addresses for {seconds:.0f}s at {hz:.0f} Hz.")
    print("Scroll down a few entries, then back up, at a normal pace. Go.")
    deadline = time.monotonic() + seconds
    period = 1.0 / hz
    while time.monotonic() < deadline:
        tick = time.monotonic()
        for a in addrs:
            v = reader(a)
            if v is not None:
                series[a].append(v)
        time.sleep(max(0.0, period - (time.monotonic() - tick)))

    scored = []
    for a, values in series.items():
        if not values:
            continue
        # Collapse to transitions: what the value actually did, ignoring dwell.
        steps = [values[0]]
        for v in values[1:]:
            if v != steps[-1]:
                steps.append(v)
        if len(steps) < 3:
            continue  # never moved, or moved once — not enough to judge
        deltas = [b - a2 for a2, b in zip(steps, steps[1:])]
        spread = max(steps) - min(steps)
        distinct = len(set(steps))

        # A cursor steps by a *constant stride* — 1 in a list, a row width in a
        # grid. What matters is that one magnitude dominates, not that it is 1.
        magnitudes = [abs(d) for d in deltas]
        stride = max(set(magnitudes), key=magnitudes.count)
        consistency = magnitudes.count(stride) / len(magnitudes)

        scored.append((consistency, -spread, distinct, a, steps, stride, len(deltas)))

    # Best behaved: consistent stride first, then a tight range.
    scored.sort(key=lambda row: (-row[0], row[1], -row[2]))
    if not scored:
        print("Nothing moved during the window.")
        return

    print(f"\n{len(scored)} address(es) moved. Best-behaved first:\n")
    for consistency, neg_spread, distinct, a, steps, stride, total in scored[:30]:
        seq = " ".join(str(s) for s in steps[:14])
        if len(steps) > 14:
            seq += " ..."
        print(f"  0x{a:08X}  range={-neg_spread:<5} distinct={distinct:<4} "
              f"stride={stride} in {consistency * 100:.0f}% of {total} moves")
        print(f"              {seq}")


def watch_for(link: DolphinLink, addr: int, kind: str, seconds: float) -> None:
    """Like scan.watch, but bounded so it can run unattended."""
    reader = getattr(link, kind, None)
    if reader is None:
        print(f"Unknown type {kind}")
        return
    print(f"Watching 0x{addr:08X} as {kind} for {seconds:.0f}s — move the cursor now.")
    deadline = time.monotonic() + seconds
    last = object()
    changes = 0
    while time.monotonic() < deadline:
        value = reader(addr)
        if value != last:
            print(f"  [{time.strftime('%H:%M:%S')}] {value}")
            last = value
            changes += 1
        time.sleep(1 / 60)
    print(f"  {changes} distinct value(s) seen.")


if __name__ == "__main__":
    sys.exit(main())
