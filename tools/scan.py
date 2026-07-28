#!/usr/bin/env python
"""Read-only memory scanner for finding game structures in Dolphin.

Cheat-Engine-style narrowing, but it lives in this repo so a found address can
be pasted straight into a Watch. It never writes to emulated memory.

Typical session for "which entry is the cursor on?":

    python tools/scan.py
    > type u8
    > new                 # start with every byte in MEM1 as a candidate
    (move the cursor down one entry in the game)
    > inc                 # keep only bytes that went up
    (move down again)
    > inc
    (move up)
    > dec
    > list                # usually a handful left
    > watch 0x805a1234    # confirm it tracks the cursor live

Commands
    type <u8|s8|u16|s16|u32|s32|f32>   value type for scanning
    new                                reset candidates to everything
    snap                               refresh the baseline without filtering
    eq <n> | ne <n> | gt <n> | lt <n>  compare against a literal
    changed | unchanged | inc | dec    compare against the baseline
    list [count]                       show surviving candidates
    watch <addr> [type]                live-print changes at one address
    dump <addr> [bytes]                hex dump
    ptr <addr>                         read a pointer and dump its target
    save <file> | quit
"""

from __future__ import annotations

import struct
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from rhfaccess.dolphin import (  # noqa: E402
    MEM1_END,
    MEM1_START,
    MEM2_END,
    MEM2_START,
    DolphinLink,
)

# name -> (struct format, byte width, numpy dtype). Big-endian throughout,
# because that is how the PowerPC in a Wii stores everything.
TYPES: Dict[str, tuple] = {
    "u8": (">B", 1, ">u1"),
    "s8": (">b", 1, "i1"),
    "u16": (">H", 2, ">u2"),
    "s16": (">h", 2, ">i2"),
    "u32": (">I", 4, ">u4"),
    "s32": (">i", 4, ">i4"),
    "f32": (">f", 4, ">f4"),
}

CHUNK = 1 << 20  # 1 MiB per read

# Reads go through DolphinLink, not dme directly, so MEM2 uses the
# ReadProcessMemory fallback. Kept module-level because Scanner gets pickled
# between steps and a live process handle cannot be.
_LINK = DolphinLink()


def _link() -> DolphinLink:
    _LINK.ensure_connected()
    return _LINK


class Scanner:
    def __init__(self, include_mem2: bool = False) -> None:
        self.regions: List[tuple] = [(MEM1_START, MEM1_END)]
        if include_mem2:
            self.regions.append((MEM2_START, MEM2_END))
        self.kind = "u8"
        self.baseline: Dict[int, bytes] = {}
        # None means "every aligned address" — never materialised as a Python
        # container, because for u8 over MEM1 that is 25 million entries.
        # Once filtered, it is a numpy uint32 array of addresses.
        self.candidates: Optional[np.ndarray] = None
        self.started = False

    # -- memory access ---------------------------------------------------

    def snapshot(self) -> Dict[int, bytes]:
        """Read every region. A region that cannot be read is dropped, not faked.

        Zero-filling a failed read would silently turn unreadable memory into
        24 MiB of plausible-looking data, and every filter downstream would
        happily "find" candidates in it.
        """
        shot: Dict[int, bytes] = {}
        dropped = []
        for start, end in self.regions:
            buf = bytearray()
            addr = start
            ok = True
            while addr < end:
                size = min(CHUNK, end - addr)
                data = _link().read(addr, size)
                if not data or len(data) != size:
                    ok = False
                    break
                buf.extend(data)
                addr += size
            if ok:
                shot[start] = bytes(buf)
            else:
                dropped.append(start)

        if dropped:
            for start in dropped:
                print(f"[warn] region 0x{start:08X} is unreadable — excluded from "
                      "this scan.")
            self.regions = [r for r in self.regions if r[0] not in dropped]
        return shot

    def value_at(self, shot: Dict[int, bytes], addr: int):
        """Single-address read out of a snapshot. Used for display, not scanning."""
        fmt, size, _ = TYPES[self.kind]
        for start, end in self.regions:
            if start <= addr and addr + size <= end:
                return struct.unpack_from(fmt, shot[start], addr - start)[0]
        return None

    def view(self, shot: Dict[int, bytes], start: int) -> np.ndarray:
        """Typed, big-endian view over one region. Zero-copy."""
        _, size, dtype = TYPES[self.kind]
        buf = shot[start]
        return np.frombuffer(buf, dtype=dtype, count=len(buf) // size)

    def region_addresses(self, start: int, count: int) -> np.ndarray:
        _, size, _ = TYPES[self.kind]
        return (start + np.arange(count, dtype=np.uint64) * size).astype(np.uint32)

    def candidate_count(self) -> int:
        if self.candidates is not None:
            return int(self.candidates.size)
        _, size, _ = TYPES[self.kind]
        return sum((e - s) // size for s, e in self.regions)

    # -- scanning --------------------------------------------------------

    def reset(self) -> None:
        self.baseline = self.snapshot()
        self.candidates = None
        self.started = True
        print(f"{self.candidate_count():,} candidates ({self.kind}, aligned). "
              "Change the value in-game, then filter.")

    def refresh(self) -> None:
        self.baseline = self.snapshot()
        print("Baseline refreshed.")

    def hold(self, samples: int = 8, seconds: float = 2.0) -> None:
        """Keep only addresses that hold *perfectly* still across many samples.

        A single `unchanged` compares two instants, so a value that flips
        between two states every frame passes it by coin flip — and there are
        millions of those (double buffers, audio, animation). Comparing N
        samples against one fixed reference drops an oscillator's survival odds
        to 2^-(N-1), which clears them out in one step.
        """
        reference = self.snapshot()
        self.baseline = reference
        gap = seconds / max(1, samples - 1)
        for i in range(max(1, samples - 1)):
            time.sleep(gap)
            self.filter("unchanged", baseline=reference, update_baseline=False,
                        quiet=(i < samples - 2))
        self.baseline = reference

    def filter(self, op: str, literal: Optional[float] = None,
               baseline: Optional[Dict[int, bytes]] = None,
               update_baseline: bool = True, quiet: bool = False) -> None:
        if not self.started:
            print("Run `new` first so there is a baseline to compare against.")
            return
        if op in ("eq", "ne", "gt", "lt") and literal is None:
            print(f"`{op}` needs a value.")
            return

        base = self.baseline if baseline is None else baseline
        current = self.snapshot()
        _, size, _ = TYPES[self.kind]
        survivors: List[np.ndarray] = []

        for start, _end in self.regions:
            now = self.view(current, start)
            was = self.view(base, start)

            if self.candidates is None:
                idx = None  # compare the whole region
            else:
                # Keep only candidates that fall inside this region, aligned.
                cand = self.candidates.astype(np.uint64)
                offs = cand - np.uint64(start)
                keep = (cand >= np.uint64(start)) & (offs < np.uint64(now.size * size))
                offs = offs[keep]
                idx = (offs // np.uint64(size)).astype(np.int64)
                if idx.size == 0:
                    continue

            now_v = now if idx is None else now[idx]
            was_v = was if idx is None else was[idx]

            if op in ("eq", "ne", "gt", "lt"):
                mask = (
                    now_v == literal if op == "eq"
                    else now_v != literal if op == "ne"
                    else now_v > literal if op == "gt"
                    else now_v < literal
                )
            else:
                mask = (
                    now_v != was_v if op == "changed"
                    else now_v == was_v if op == "unchanged"
                    else now_v > was_v if op == "inc"
                    else now_v < was_v
                )

            if idx is None:
                hits = np.flatnonzero(mask)
                survivors.append(self.region_addresses(start, now.size)[hits])
            else:
                survivors.append(
                    (np.uint64(start) + idx[mask].astype(np.uint64) * size).astype(np.uint32)
                )

        self.candidates = (
            np.concatenate(survivors) if survivors else np.empty(0, dtype=np.uint32)
        )
        if update_baseline:
            self.baseline = current
        if not quiet:
            print(f"{self.candidate_count():,} candidates left.")

    def list(self, count: int = 20) -> None:
        if self.candidates is None:
            print("Everything is still a candidate — run a filter "
                  "(changed / unchanged / inc / dec / eq) first.")
            return
        if self.candidates.size == 0:
            print("No candidates left. Run `new` to start over.")
            return
        current = self.snapshot()
        for addr in self.candidates[:count]:
            print(f"  0x{int(addr):08X} = {self.value_at(current, int(addr))}")
        if self.candidates.size > count:
            print(f"  ... and {self.candidates.size - count:,} more")

    def save(self, path: str) -> None:
        if self.candidates is None or self.candidates.size == 0:
            print("Nothing to save — filter down to a candidate list first.")
            return
        with open(path, "w", encoding="utf-8") as fh:
            for addr in self.candidates:
                fh.write(f"0x{int(addr):08X}\n")
        print(f"Wrote {self.candidates.size:,} addresses to {path}")


def watch(link: DolphinLink, addr: int, kind: str) -> None:
    reader = getattr(link, kind, None)
    if reader is None:
        print(f"Unknown type {kind}")
        return
    print(f"Watching 0x{addr:08X} as {kind}. Ctrl+C to stop.")
    last = object()
    try:
        while True:
            value = reader(addr)
            if value != last:
                stamp = time.strftime("%H:%M:%S")
                print(f"  [{stamp}] {value}")
                last = value
            time.sleep(1 / 60)
    except KeyboardInterrupt:
        print("  stopped.")


def dump(link: DolphinLink, addr: int, length: int = 64) -> None:
    data = link.read(addr, length)
    if data is None:
        print("Unreadable.")
        return
    for i in range(0, len(data), 16):
        row = data[i:i + 16]
        hexes = " ".join(f"{b:02X}" for b in row)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        print(f"  0x{addr + i:08X}  {hexes:<47}  {text}")


def parse_number(token: str) -> Optional[float]:
    try:
        if token.lower().startswith("0x"):
            return int(token, 16)
        if "." in token:
            return float(token)
        return int(token, 10)
    except ValueError:
        return None


def main() -> int:
    include_mem2 = "--mem2" in sys.argv
    link = DolphinLink()
    if not link.ensure_connected():
        print("Could not hook Dolphin. Is it running with a game loaded?")
        return 1
    print(f"Hooked. Game: {link.game_id()} — {link.game_title()}")
    if include_mem2:
        print("Scanning MEM1 + MEM2 (slower).")

    scanner = Scanner(include_mem2=include_mem2)
    print("Type `help` for commands.")

    while True:
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not raw:
            continue
        parts = raw.split()
        cmd, args = parts[0].lower(), parts[1:]

        if cmd in ("quit", "exit", "q"):
            return 0
        if cmd == "help":
            print(__doc__)
        elif cmd == "type":
            if args and args[0] in TYPES:
                scanner.kind = args[0]
                scanner.candidates = None
                scanner.started = False
                print(f"Type is {scanner.kind}. Run `new` to start a scan.")
            else:
                print("Types: " + ", ".join(TYPES))
        elif cmd == "new":
            scanner.reset()
        elif cmd == "snap":
            scanner.refresh()
        elif cmd in ("changed", "unchanged", "inc", "dec"):
            scanner.filter(cmd)
        elif cmd in ("eq", "ne", "gt", "lt"):
            value = parse_number(args[0]) if args else None
            if value is None:
                print(f"Usage: {cmd} <number>")
            else:
                scanner.filter(cmd, value)
        elif cmd == "list":
            count = int(args[0]) if args and args[0].isdigit() else 20
            scanner.list(count)
        elif cmd == "watch":
            addr = parse_number(args[0]) if args else None
            kind = args[1] if len(args) > 1 else scanner.kind
            if addr is None:
                print("Usage: watch <addr> [type]")
            else:
                watch(link, int(addr), kind)
        elif cmd == "dump":
            addr = parse_number(args[0]) if args else None
            length = int(parse_number(args[1]) or 64) if len(args) > 1 else 64
            if addr is None:
                print("Usage: dump <addr> [bytes]")
            else:
                dump(link, int(addr), length)
        elif cmd == "ptr":
            addr = parse_number(args[0]) if args else None
            if addr is None:
                print("Usage: ptr <addr>")
            else:
                target = link.pointer(int(addr))
                if target is None:
                    print("Not a valid pointer.")
                else:
                    print(f"  -> 0x{target:08X}")
                    dump(link, target, 64)
        elif cmd == "save":
            scanner.save(args[0] if args else "candidates.txt")
        else:
            print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    sys.exit(main())
