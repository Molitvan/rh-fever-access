"""Runtime locator for the game's DAT1 text archive.

Every useful string in Rhythm Heaven Fever lives in a "DAT1" blob on the MEM2
heap, indexed by a table of 8-byte records sitting just before it. Both move
when the game reboots or changes scene, so nothing here may be hardcoded — the
archive is found by its magic and then *proved* before use.

Layout, as observed:

    <index table>   8-byte records, ascending u32 offsets, then zero padding
    "DAT1"          magic
    u32 size        total archive size
    <strings>       UTF-16BE, NUL-terminated, concatenated; offsets are
                    relative to the first string, i.e. magic + 8
"""

from __future__ import annotations

import struct
from typing import List, Optional

MAGIC = b"DAT1"
MEM2_START = 0x90000000
MEM2_SIZE = 0x4000000
CHUNK = 1 << 22

MAX_RECORDS = 4096
MIN_RECORDS = 32


class Archive:
    """A located, validated text archive."""

    def __init__(self, link, magic_addr: int, size: int,
                 table_addr: int, count: int) -> None:
        self._link = link
        self.magic_addr = magic_addr
        self.size = size
        self.text_base = magic_addr + 8
        self.table_addr = table_addr
        self.count = count
        self.short_ratio = 0.0  # set during validation; see find()

    def offset(self, index: int) -> Optional[int]:
        if not (0 <= index < self.count):
            return None
        off = self._link.u32(self.table_addr + 8 * index)
        if off is None or not (0 <= off < self.size):
            return None
        return off

    def text(self, index: int, max_bytes: int = 512) -> Optional[str]:
        off = self.offset(index)
        if off is None:
            return None
        raw = self._link.read(self.text_base + off, max_bytes)
        if not raw:
            return None
        end = 0
        while end < len(raw) - 1 and raw[end:end + 2] != b"\x00\x00":
            end += 2
        if end == 0:
            return None
        try:
            return raw[:end].decode("utf-16-be")
        except UnicodeDecodeError:
            return None

    def still_valid(self) -> bool:
        """Cheap re-check that the heap has not moved under us."""
        return self._link.read(self.magic_addr, 4) == MAGIC


def _build(link, magic_addr: int) -> Optional[Archive]:
    """Walk the index table backwards from a magic hit and validate it."""
    size = link.u32(magic_addr + 4)
    if size is None or not (0x100 <= size < MEM2_SIZE):
        return None

    # Records sit before the magic, past a little zero padding.
    addr = magic_addr - 8
    while addr > magic_addr - 0x40:
        if link.u32(addr) not in (0, None):
            break
        addr -= 8
    if link.u32(addr) is None:
        return None

    # Walk back while offsets stay ordered and inside the archive.
    last = addr
    prev = link.u32(addr)
    if prev is None or prev >= size:
        return None
    start = addr
    while start - 8 > magic_addr - MAX_RECORDS * 8:
        off = link.u32(start - 8)
        if off is None or off > prev or off >= size:
            break
        start -= 8
        prev = off

    count = (last - start) // 8 + 1
    if count < MIN_RECORDS:
        return None

    archive = Archive(link, magic_addr, size, start, count)

    # Prove it: a real archive decodes almost everything it points at.
    sample = [archive.text(i) for i in range(0, count, max(1, count // 32))]
    good = [s for s in sample if s]
    if len(good) < len(sample) * 0.8:
        return None

    # A title archive is short labels; a script archive is sentences. This
    # separates them without depending on the game's language.
    archive.short_ratio = sum(1 for s in good if len(s) <= 24) / len(good)
    return archive


def find(link, want_titles: bool = True) -> Optional[Archive]:
    """Scan MEM2 for text archives.

    With want_titles, prefer the archive that looks like a table of labels
    rather than a script — the game keeps several DAT1 blobs resident at once
    and the biggest is usually dialogue, not menu names.
    """
    found: List[Archive] = []
    addr = MEM2_START
    # Stop where the mapping does. Reading past a 56 MiB MEM2 fails on every
    # chunk, which costs nothing but is also the one shape of failure worth not
    # confusing with a real one.
    end = MEM2_START + min(MEM2_SIZE, link.mem2_extent())
    tail = b""
    tail_addr = addr

    while addr < end:
        block = link.read(addr, min(CHUNK, end - addr))
        if not block:
            addr += CHUNK
            tail = b""
            continue
        buf = tail + block
        base = tail_addr if tail else addr
        i = buf.find(MAGIC)
        while i != -1:
            hit = base + i
            if hit % 4 == 0:
                archive = _build(link, hit)
                if archive is not None:
                    found.append(archive)
            i = buf.find(MAGIC, i + 1)
        tail = block[-3:]
        tail_addr = addr + len(block) - 3
        addr += CHUNK

    if not found:
        return None
    if not want_titles:
        return max(found, key=lambda a: a.count)
    titled = [a for a in found if a.count >= 100 and a.short_ratio >= 0.6]
    if titled:
        return max(titled, key=lambda a: (a.short_ratio, a.count))
    return max(found, key=lambda a: a.count)
