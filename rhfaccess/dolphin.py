"""Thin, defensive wrapper around dolphin-memory-engine.

Every read can fail (emulation paused, game booting, Dolphin closed, address
temporarily unmapped). Nothing above this layer should ever see an exception:
a failed read returns None, and callers treat None as "unverified", which means
"stay silent" rather than "guess".
"""

from __future__ import annotations

import struct
import time
from typing import Optional

import dolphin_memory_engine as dme

from .rawmem import RawMemory

# Wii physical memory as Dolphin exposes it.
MEM1_START = 0x80000000
MEM1_END = 0x81800000  # 24 MiB
MEM2_START = 0x90000000
MEM2_END = 0x94000000  # 64 MiB

# Disc header, copied to the start of MEM1 at boot.
ADDR_GAME_ID = 0x80000000  # 6 bytes, e.g. b"SOME01"
ADDR_GAME_TITLE = 0x80000020  # NUL-terminated ASCII


def in_ram(addr: int) -> bool:
    """True if addr looks like a real Wii RAM address."""
    return MEM1_START <= addr < MEM1_END or MEM2_START <= addr < MEM2_END


class DolphinLink:
    """A reconnecting handle on Dolphin's emulated RAM."""

    def __init__(self, retry_seconds: float = 1.0) -> None:
        self._retry_seconds = retry_seconds
        self._next_retry = 0.0
        self._was_connected = False
        # dolphin-memory-engine cannot reach MEM2 on current Dolphin builds,
        # so those reads go through a direct ReadProcessMemory backend.
        self._raw = RawMemory()
        self._raw_tried = False

    # -- connection ------------------------------------------------------

    @property
    def connected(self) -> bool:
        return dme.is_hooked()

    def ensure_connected(self) -> bool:
        """Hook Dolphin if needed. Rate-limited so the poll loop stays cheap."""
        if dme.is_hooked():
            # The hook can go stale without an error; a probe read confirms it.
            if self.u32(ADDR_GAME_ID) is not None:
                return True
            dme.un_hook()

        now = time.monotonic()
        if now < self._next_retry:
            return False
        self._next_retry = now + self._retry_seconds

        try:
            dme.hook()
        except Exception:
            return False
        if dme.is_hooked():
            # A fresh hook means a possibly-restarted Dolphin: the old MEM2
            # base is meaningless now.
            self._raw.close()
            self._raw_tried = False
            return True
        return False

    def disconnect(self) -> None:
        if dme.is_hooked():
            dme.un_hook()
        self._raw.close()
        self._raw_tried = False

    def note_connection_change(self) -> Optional[bool]:
        """Return True/False the first time connection state flips, else None."""
        now = self.connected
        if now != self._was_connected:
            self._was_connected = now
            return now
        return None

    # -- raw reads -------------------------------------------------------

    def read(self, addr: int, size: int) -> Optional[bytes]:
        if size <= 0 or not in_ram(addr) or not in_ram(addr + size - 1):
            return None

        if addr >= MEM2_START:
            return self._read_mem2(addr, size)

        try:
            data = dme.read_bytes(addr, size)
        except Exception:
            return None
        if data is None or len(data) != size:
            return None
        return data

    def _read_mem2(self, addr: int, size: int) -> Optional[bytes]:
        if not self._raw.available:
            if self._raw_tried:
                return None
            self._raw_tried = True
            game_id = self.read(ADDR_GAME_ID, 6)
            if not self._raw.attach(game_id):
                return None
        return self._raw.read(addr, size)

    # -- typed reads (PowerPC is big-endian) -----------------------------

    def u8(self, addr: int) -> Optional[int]:
        data = self.read(addr, 1)
        return None if data is None else data[0]

    def s8(self, addr: int) -> Optional[int]:
        data = self.read(addr, 1)
        return None if data is None else struct.unpack(">b", data)[0]

    def u16(self, addr: int) -> Optional[int]:
        data = self.read(addr, 2)
        return None if data is None else struct.unpack(">H", data)[0]

    def s16(self, addr: int) -> Optional[int]:
        data = self.read(addr, 2)
        return None if data is None else struct.unpack(">h", data)[0]

    def u32(self, addr: int) -> Optional[int]:
        data = self.read(addr, 4)
        return None if data is None else struct.unpack(">I", data)[0]

    def s32(self, addr: int) -> Optional[int]:
        data = self.read(addr, 4)
        return None if data is None else struct.unpack(">i", data)[0]

    def f32(self, addr: int) -> Optional[float]:
        data = self.read(addr, 4)
        return None if data is None else struct.unpack(">f", data)[0]

    # -- pointers and strings -------------------------------------------

    def pointer(self, addr: int) -> Optional[int]:
        """Read a pointer and return it only if it points into real RAM."""
        value = self.u32(addr)
        if value is None or not in_ram(value):
            return None
        return value

    def chase(self, base: int, *offsets: int) -> Optional[int]:
        """Follow a pointer chain, validating every hop. None if any hop fails."""
        addr = self.pointer(base)
        if addr is None:
            return None
        if not offsets:
            return addr
        for offset in offsets[:-1]:
            addr = self.pointer(addr + offset)
            if addr is None:
                return None
        addr += offsets[-1]
        return addr if in_ram(addr) else None

    def cstring(self, addr: int, max_len: int = 64, encoding: str = "utf-8") -> Optional[str]:
        data = self.read(addr, max_len)
        if data is None:
            return None
        end = data.find(b"\x00")
        if end == -1:
            end = len(data)
        try:
            return data[:end].decode(encoding, errors="strict")
        except UnicodeDecodeError:
            return None

    # -- identity --------------------------------------------------------

    def game_id(self) -> Optional[str]:
        data = self.read(ADDR_GAME_ID, 6)
        if data is None:
            return None
        try:
            game_id = data.decode("ascii", errors="strict")
        except UnicodeDecodeError:
            return None
        return game_id if game_id.isalnum() else None

    def game_title(self) -> Optional[str]:
        title = self.cstring(ADDR_GAME_TITLE, 64, "ascii")
        if not title:
            return None
        return title.strip() or None
