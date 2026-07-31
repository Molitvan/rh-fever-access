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

    # -- lookup ----------------------------------------------------------

    def address(self, name: str) -> Optional[int]:
        addr = self._addresses.get(name)
        if addr is not None and self._name_at(addr) == name:
            return addr
        self._addresses.pop(name, None)
        return None

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

    def scan(self, wanted: Optional[Iterable[str]] = None) -> Dict[str, int]:
        """Sweep MEM2 for pane names. ~1s, so this is not a per-frame operation."""
        targets = set(wanted) if wanted is not None else None
        found: Dict[str, int] = {}
        addr = MEM2_START
        tail = b""
        tail_addr = addr

        while addr < MEM2_START + MEM2_SIZE:
            block = self._link.read(addr, min(CHUNK, MEM2_START + MEM2_SIZE - addr))
            if not block:
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

        self._addresses.update(found)
        return found
