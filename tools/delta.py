#!/usr/bin/env python
"""Filter scan candidates by how much they changed, not just whether they did.

A counter like Flow moves by a few points per game. Almost everything else that
survives a "changed after every game" filter is a timer, a frame count or RNG,
which jumps by huge amounts. Comparing deltas separates them in one step.

    python tools/delta.py snap                 # record current values
    (play a game)
    python tools/delta.py cmp --max-delta 15   # keep small movers

Works on whatever candidate list step.py currently holds.
"""

from __future__ import annotations

import pickle
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import numpy as np  # noqa: E402

from scan import TYPES, Scanner  # noqa: E402

STATE = HERE.parent / ".scanstate.pkl"
VALUES = HERE.parent / ".deltavalues.npz"


def read_values(scanner: Scanner):
    """Current value of every candidate, as a numpy array."""
    shot = scanner.snapshot()
    _fmt, size, _dtype = TYPES[scanner.kind]
    addrs = scanner.candidates
    out = np.zeros(addrs.size, dtype=np.int64)
    for start, _end in scanner.regions:
        view = scanner.view(shot, start)
        cand = addrs.astype(np.int64)
        offs = cand - start
        inside = (offs >= 0) & (offs < view.size * size)
        idx = (offs[inside] // size).astype(np.int64)
        out[inside] = view[idx].astype(np.int64)
    return addrs.copy(), out


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]

    max_delta = 15
    if "--max-delta" in argv:
        max_delta = int(argv[argv.index("--max-delta") + 1])
    lo, hi = 0, 999
    if "--range" in argv:
        i = argv.index("--range")
        lo, hi = int(argv[i + 1]), int(argv[i + 2])

    if not STATE.exists():
        print("No scan state. Run step.py new first.")
        return 1
    scanner: Scanner = pickle.load(open(STATE, "rb"))
    if scanner.candidates is None:
        print("Candidates not narrowed yet; run a filter first.")
        return 1

    if cmd == "snap":
        addrs, vals = read_values(scanner)
        np.savez(VALUES, addrs=addrs, vals=vals)
        print(f"recorded {addrs.size:,} values")
        return 0

    if cmd == "cmp":
        if not VALUES.exists():
            print("No recorded values; run `snap` first.")
            return 1
        saved = np.load(VALUES)
        old_addrs, old_vals = saved["addrs"], saved["vals"]
        addrs, vals = read_values(scanner)
        if not np.array_equal(addrs, old_addrs):
            print("Candidate list changed since snap; re-run snap.")
            return 1

        delta = np.abs(vals - old_vals)
        keep = (delta >= 1) & (delta <= max_delta) & (vals >= lo) & (vals <= hi) \
            & (old_vals >= lo) & (old_vals <= hi)
        survivors = addrs[keep]
        print(f"{survivors.size:,} moved by 1..{max_delta} and stayed in {lo}..{hi}")

        scanner.candidates = survivors.astype(np.uint32)
        pickle.dump(scanner, open(STATE, "wb"), protocol=pickle.HIGHEST_PROTOCOL)
        np.savez(VALUES, addrs=survivors, vals=vals[keep])

        order = np.argsort(-np.abs(vals[keep] - old_vals[keep]))
        for i in order[:25]:
            a = int(survivors[i])
            print(f"   0x{a:08X}  {int(old_vals[keep][i])} -> {int(vals[keep][i])}")
        return 0

    print(f"Unknown command {cmd}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
