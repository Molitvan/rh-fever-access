"""Reader for the game's on-screen text panes.

Rhythm Heaven Fever draws its UI with Nintendo's NW4R layout system. Every text
box is a runtime object that carries its own ASCII pane name ("T_game_title_00",
"T_exposition_00", ...) followed, at a fixed offset, by a pointer to the
UTF-16BE string it is currently displaying.

That is far better than chasing individual buffers: the pane name is a stable,
self-describing handle, so text can be read by asking for it by name. It should
generalise to dialogue, results and other screens, not just the info card.

The panes live on the MEM2 heap and move, so they are located by scanning for
the name once and then re-validated cheaply on every read.

Caveat: a pane keeps its last string after the screen that owned it goes away.
Nothing here can tell you whether a pane is *visible* — the caller must gate on
game state before speaking, or it will happily read stale text.
"""

from __future__ import annotations

import re
import time
from typing import Dict, Iterable, Optional

MEM2_START = 0x90000000
MEM2_SIZE = 0x4000000
CHUNK = 1 << 22

# "T_<name>\0", 4-byte aligned, immediately inside the pane object.
NAME_PATTERN = re.compile(rb"T_[A-Za-z0-9_]{2,30}\x00")
TEXT_POINTER_OFFSET = 0x1C
MAX_TEXT_BYTES = 512

# Effective alpha, in the pane's own header three bytes ahead of its name.
# It is 0 while the pane is not being drawn and ramps up as it fades in, so it
# answers the question the rest of this module cannot: not "does this pane
# exist" but "is the player actually looking at it".
#
# Verified on the cafe's dialogue box, which fades 0x00 -> 0x83 -> 0xDC as it
# opens and back to 0x00 as it closes, while the menu buttons beside it sit at
# 0xFF throughout and a hidden button sits at 0x00.
ALPHA_OFFSET = -3

# It settles at 0xDC on that dialogue box rather than 0xFF, so this is a
# threshold and not an equality test. Well clear of a fade's early frames.
VISIBLE_ALPHA = 0x40

# Private-use glyphs stand in for controller buttons in the game's font.
BUTTON_GLYPHS = {
    "": "A",   # A button
    "": "B",   # B button
}
PRIVATE_USE = re.compile("[-]")


def clean(text: str) -> str:
    """Make pane text speakable: button glyphs named, newlines flattened."""
    for glyph, name in BUTTON_GLYPHS.items():
        text = text.replace(glyph, name)
    text = PRIVATE_USE.sub("", text)
    return " ".join(text.split())


class PaneIndex:
    """Locates text panes by name and reads their live strings."""

    def __init__(self, link, rescan_interval: float = 3.0) -> None:
        self._link = link
        self._addresses: Dict[str, int] = {}
        self._rescan_interval = rescan_interval
        self._next_scan = 0.0
        self._generation = getattr(link, "generation", 0)

    # -- lookup ----------------------------------------------------------

    def _check_generation(self) -> None:
        """Drop every cached address when the emulated memory is remapped.

        Re-attaching to Dolphin hands out a whole new mapping, so every address
        learned before it describes somewhere that is no longer the game. The
        name check in `address()` is not enough on its own: it re-reads the same
        location, and the odds of a plausible pane name being there are small
        but they are not the odds we want on the wrong side of `text()`.
        """
        generation = getattr(self._link, "generation", 0)
        if generation != self._generation:
            self._generation = generation
            self._addresses.clear()
            self._next_scan = 0.0

    def address(self, name: str) -> Optional[int]:
        self._check_generation()
        addr = self._addresses.get(name)
        if addr is not None and self._name_at(addr) == name:
            return addr
        self._addresses.pop(name, None)
        return None

    def live(self, name: str) -> bool:
        """True if the pane existed at the last full sweep and still validates.

        The absence of a pane is a usable signal — the file select can be told
        from the game menu by the menu's card panes being gone — but only
        because `scan()` drops what it no longer finds. Without that, this
        would answer "yes" forever: freeing a layout leaves the ASCII name
        lying in the heap, and `address()` re-checks nothing else.
        """
        return self.address(name) is not None

    def text(self, name: str) -> Optional[str]:
        addr = self.address(name)
        if addr is None:
            return None
        pointer = self._link.u32(addr + TEXT_POINTER_OFFSET)
        if pointer is None or not (MEM2_START <= pointer < MEM2_START + MEM2_SIZE):
            return None
        raw = self._link.read(pointer, MAX_TEXT_BYTES)
        if not raw:
            return None
        end = 0
        while end < len(raw) - 1 and raw[end:end + 2] != b"\x00\x00":
            end += 2
        if end < 2:
            return None
        try:
            decoded = raw[:end].decode("utf-16-be")
        except UnicodeDecodeError:
            return None
        text = clean(decoded)
        return text or None

    def alpha(self, name: str) -> Optional[int]:
        """The pane's effective alpha, or None if it cannot be read."""
        addr = self.address(name)
        if addr is None:
            return None
        data = self._link.read(addr + ALPHA_OFFSET, 1)
        return None if data is None else data[0]

    def visible(self, name: str, threshold: int = VISIBLE_ALPHA) -> bool:
        """True if the pane is actually on screen, not merely resident.

        Stronger than `live()`, and the right gate whenever a screen leaves its
        panes behind holding the last thing they showed — which most of them
        do. Being a threshold on a fade, it is briefly false at the start of an
        appearance; the engine's stability requirement covers that.
        """
        value = self.alpha(name)
        return value is not None and value >= threshold

    def _name_at(self, addr: int) -> Optional[str]:
        raw = self._link.read(addr, 34)
        if not raw:
            return None
        end = raw.find(b"\x00")
        if end <= 2:
            return None
        try:
            return raw[:end].decode("ascii")
        except UnicodeDecodeError:
            return None

    # -- discovery -------------------------------------------------------

    def ensure(self, names: Iterable[str]) -> bool:
        """Make sure every requested pane is located. Rescans are rate-limited."""
        wanted = [n for n in names if self.address(n) is None]
        if not wanted:
            return True
        now = time.monotonic()
        if now < self._next_scan:
            return False
        self._next_scan = now + self._rescan_interval
        # A full sweep, not one filtered to `wanted`: it costs the same (the
        # expense is reading MEM2, not matching names) and it caches every pane
        # on screen, so other probes need not sweep for their own.
        self.scan()
        return all(self.address(n) is not None for n in names)

    def scan(self, wanted: Optional[Iterable[str]] = None) -> Optional[Dict[str, int]]:
        """Sweep MEM2 for pane names, or None if MEM2 could not be read.

        ~0.14s, so this is not a per-frame operation.

        The None is the important part. An empty result is *evidence* here —
        "this screen has no text on it" is how the title screen is recognised,
        and a pane going missing is how the file select is told from the game
        menu — so a sweep that simply could not read MEM2 must never be allowed
        to look like one. When the raw backend goes stale (the game restarted,
        Dolphin remapped its arena) every read below returns None, and reporting
        that as an empty screen announced the title screen on top of the game
        menu while silencing every probe that reads text.
        """
        self._check_generation()
        targets = set(wanted) if wanted is not None else None
        found: Dict[str, int] = {}
        addr = MEM2_START
        end = MEM2_START + min(MEM2_SIZE, self._link.mem2_extent())
        tail = b""
        tail_addr = addr
        complete = True

        while addr < end:
            block = self._link.read(addr, min(CHUNK, end - addr))
            if not block:
                complete = False
                addr += CHUNK
                tail = b""
                continue
            buf = tail + block
            base = tail_addr if tail else addr
            for match in NAME_PATTERN.finditer(buf):
                offset = match.start()
                pane = base + offset
                if pane % 4:
                    continue
                name = match.group()[:-1].decode("ascii")
                if targets is not None and name not in targets:
                    continue
                # Keep the first pane of a given name that has a usable pointer.
                if name in found:
                    continue
                pointer = self._link.u32(pane + TEXT_POINTER_OFFSET)
                if pointer and MEM2_START <= pointer < MEM2_START + MEM2_SIZE:
                    found[name] = pane
            tail = block[-34:]
            tail_addr = addr + len(block) - 34
            addr += CHUNK

        if not complete:
            # Whatever this did manage to see is still worth keeping — a pane
            # found is a pane found. What it cannot do is prove a pane is gone,
            # so it neither forgets nor reports a count.
            self._addresses.update(found)
            return None

        if targets is None:
            # An unfiltered sweep saw everything there is, so it is allowed to
            # forget. A pane that has gone was freed with its layout, and
            # keeping its address would let text() decode whatever now sits in
            # that heap — the name survives the free, so address() alone cannot
            # notice. Callers depend on this to use absence as evidence.
            self._addresses = dict(found)
        else:
            self._addresses.update(found)
        return found
