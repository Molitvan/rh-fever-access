"""The probe engine: read, corroborate, then speak.

A probe never speaks directly. It returns a *snapshot* of the piece of game
state it owns, or None meaning "I could not verify this right now". The engine
does the rest:

  * A snapshot must repeat identically for `stable_ticks` polls before it is
    trusted. This kills the single-frame garbage you get while a structure is
    being rebuilt, and it costs a few milliseconds, not a frame.
  * Only a change from one trusted snapshot to the next produces speech.
  * Sustained failure to verify clears the trusted snapshot, so re-entering a
    screen announces it again instead of staying silent.

The result is the behaviour we want: when in doubt, say nothing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Hashable, Iterable, List, Optional


@dataclass(frozen=True)
class Utterance:
    text: str
    interrupt: bool = False
    priority: int = 0  # higher is spoken first within a tick


class Probe:
    """One observable slice of game state."""

    name: str = "probe"
    interval: float = 0.0  # minimum seconds between reads; 0 = every tick
    stable_ticks: int = 2  # identical reads required before the value is trusted
    forget_after: int = 4  # unverified reads before the trusted value is dropped

    def read(self, link) -> Optional[Hashable]:
        """Return a hashable snapshot, or None if it cannot be verified."""
        raise NotImplementedError

    def describe(self, previous: Optional[Any], current: Any) -> Iterable[Utterance]:
        """Turn a confirmed transition into speech. `previous` is None on first sight."""
        return ()

    def reset(self) -> None:
        """Hook for probes holding their own scratch state."""


@dataclass
class _ProbeState:
    confirmed: Optional[Hashable] = None
    candidate: Optional[Hashable] = None
    candidate_hits: int = 0
    unverified: int = 0
    next_read: float = 0.0
    seen: bool = False


class ProbeEngine:
    def __init__(self, probes: Iterable[Probe]) -> None:
        self.probes: List[Probe] = list(probes)
        self._state = {id(p): _ProbeState() for p in self.probes}

    def reset(self) -> None:
        """Forget everything — used on disconnect or when the game changes."""
        for probe in self.probes:
            probe.reset()
        self._state = {id(p): _ProbeState() for p in self.probes}

    def tick(self, link) -> List[Utterance]:
        now = time.monotonic()
        out: List[Utterance] = []

        for probe in self.probes:
            state = self._state[id(probe)]
            if now < state.next_read:
                continue
            state.next_read = now + probe.interval

            try:
                snapshot = probe.read(link)
            except Exception:
                snapshot = None  # a broken probe must not take the loop down

            if snapshot is None:
                state.candidate = None
                state.candidate_hits = 0
                state.unverified += 1
                if state.seen and state.unverified >= probe.forget_after:
                    state.confirmed = None
                    state.seen = False
                continue

            state.unverified = 0

            if snapshot == state.candidate:
                state.candidate_hits += 1
            else:
                state.candidate = snapshot
                state.candidate_hits = 1

            if state.candidate_hits < probe.stable_ticks:
                continue
            if state.seen and snapshot == state.confirmed:
                continue

            previous = state.confirmed if state.seen else None
            state.confirmed = snapshot
            state.seen = True
            try:
                out.extend(probe.describe(previous, snapshot))
            except Exception:
                continue

        out.sort(key=lambda u: -u.priority)
        return out
