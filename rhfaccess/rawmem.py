"""Direct reader for Dolphin's emulated MEM2.

dolphin-memory-engine reads MEM1 fine against current Dolphin builds but
reports MEM2 as absent, and every read into 0x90000000+ throws. Most of a Wii
game's heap — including Rhythm Heaven Fever's menu entry objects — lives there,
so the companion would be blind to almost everything without it.

This finds Dolphin's emulated RAM in the host process directly:

  * MEM1 is a MAPPED region whose first six bytes are the disc's game ID.
  * Dolphin keeps several mirrors of the same memory. In the fastmem arena the
    host offset matches the logical one, so MEM2 sits at MEM1 + 0x10000000
    (the 0x80000000 -> 0x90000000 gap). In the plain mapping the two regions
    are simply adjacent, MEM1 + 0x2000000.

Both layouts are probed and validated by region size before use. Read-only:
this opens the process with PROCESS_VM_READ and nothing else.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import List, Optional, Tuple

k32 = ctypes.WinDLL("kernel32", use_last_error=True)

PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MEM_COMMIT = 0x1000
MEM_MAPPED = 0x40000
TH32CS_SNAPPROCESS = 0x0002

MEM1_LOGICAL = 0x80000000
MEM2_LOGICAL = 0x90000000
MEM1_SIZES = (0x2000000, 0x1800000)  # 32 MiB arena slot, or exactly 24 MiB
MEM2_SIZES = (0x4000000, 0x3800000)  # 64 MiB arena slot, or exactly 56 MiB
MEM2_OFFSETS = (0x10000000, 0x2000000)


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("__alignment1", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("__alignment2", wintypes.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def find_processes(name: str = "Dolphin.exe") -> List[int]:
    """Every Dolphin process, not just the first.

    More than one can be open at a time, and typically only one has a game
    running. Picking blindly gets you the empty one.
    """
    out: List[int] = []
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return out
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snap, ctypes.byref(entry)):
            return out
        while True:
            if entry.szExeFile.lower() == name.lower():
                out.append(int(entry.th32ProcessID))
            if not k32.Process32NextW(snap, ctypes.byref(entry)):
                return out
    finally:
        k32.CloseHandle(snap)


class RawMemory:
    """Read-only view of Dolphin's emulated MEM1/MEM2 via ReadProcessMemory."""

    def __init__(self) -> None:
        self.pid: Optional[int] = None
        self._handle = None
        self.mem1_base: Optional[int] = None
        self.mem2_base: Optional[int] = None
        # How much of each region Dolphin actually mapped. Sweeps need this:
        # MEM2 is 64 MiB logically but can be mapped as 56, and reads past the
        # end fail exactly like a dead backend does.
        self.mem1_size: int = 0
        self.mem2_size: int = 0
        self.game_id: Optional[bytes] = None

    # -- setup -----------------------------------------------------------

    def attach(self, game_id: Optional[bytes] = None) -> bool:
        """Attach to whichever Dolphin actually has a game running."""
        self.close()
        for pid in find_processes():
            if self._attach_pid(pid, game_id):
                return True
            self.close()
        return False

    def _attach_pid(self, pid: int, game_id: Optional[bytes]) -> bool:
        handle = k32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
                                 False, pid)
        if not handle:
            return False
        self.pid, self._handle = pid, handle

        regions = self._regions()
        sizes = {base: size for base, size, _t in regions}

        for base, size, mtype in regions:
            if mtype != MEM_MAPPED or size not in MEM1_SIZES:
                continue
            head = self._read_raw(base, 6)
            # A running game puts its disc ID at the start of MEM1. An idle
            # Dolphin has no such region, which is what tells the instances
            # apart.
            if not head or not head.isalnum():
                continue
            if game_id is not None and head != game_id:
                continue
            for offset in MEM2_OFFSETS:
                candidate = base + offset
                mem2_size = sizes.get(candidate)
                if mem2_size in MEM2_SIZES:
                    self.mem1_base, self.mem2_base = base, candidate
                    self.mem1_size, self.mem2_size = size, mem2_size
                    self.game_id = head
                    return True
        return False

    def close(self) -> None:
        if self._handle:
            k32.CloseHandle(self._handle)
        self._handle = None
        self.pid = self.mem1_base = self.mem2_base = self.game_id = None
        self.mem1_size = self.mem2_size = 0

    @property
    def available(self) -> bool:
        return self._handle is not None and self.mem2_base is not None

    def healthy(self) -> bool:
        """True if this attachment still reads the game it attached to.

        `available` cannot answer that. Restarting the game — or Dolphin
        re-creating its arena for any other reason — moves the emulated RAM,
        while the process handle stays open and every base recorded here goes
        on pointing at memory that is no longer the game. Reads then fail
        forever with nothing above noticing, because MEM1 falls back to
        dolphin-memory-engine and only MEM2 visibly breaks.

        The disc ID is the cheapest proof: six bytes that must still be there.
        """
        if not self.available:
            return False
        return self.read(MEM1_LOGICAL, 6) == self.game_id

    def _regions(self) -> List[Tuple[int, int, int]]:
        out: List[Tuple[int, int, int]] = []
        mbi = MEMORY_BASIC_INFORMATION()
        addr = 0
        while addr < 0x7FFFFFFFFFFF:
            if not k32.VirtualQueryEx(self._handle, ctypes.c_void_p(addr),
                                      ctypes.byref(mbi), ctypes.sizeof(mbi)):
                break
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize
            if size == 0:
                break
            if mbi.State == MEM_COMMIT:
                out.append((base, size, mbi.Type))
            addr = base + size
        return out

    # -- reads -----------------------------------------------------------

    def _read_raw(self, host_addr: int, size: int) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(size)
        got = ctypes.c_size_t(0)
        ok = k32.ReadProcessMemory(self._handle, ctypes.c_void_p(host_addr), buf,
                                   ctypes.c_size_t(size), ctypes.byref(got))
        if not ok or got.value != size:
            return None
        return buf.raw[:size]

    def host_address(self, logical: int) -> Optional[int]:
        # Bounded by what was actually mapped, not by the logical size of the
        # region. A 56 MiB MEM2 has no host memory behind its last 8 MiB, and
        # a read there fails in a way indistinguishable from the backend being
        # dead — which callers now treat as "cannot verify" and go quiet over.
        if self.mem2_base is not None and MEM2_LOGICAL <= logical < MEM2_LOGICAL + self.mem2_size:
            return self.mem2_base + (logical - MEM2_LOGICAL)
        if self.mem1_base is not None and MEM1_LOGICAL <= logical < MEM1_LOGICAL + min(self.mem1_size, 0x1800000):
            return self.mem1_base + (logical - MEM1_LOGICAL)
        return None

    def read(self, logical: int, size: int) -> Optional[bytes]:
        if not self.available or size <= 0:
            return None
        host = self.host_address(logical)
        # The last byte must land in the same region, or this would read off
        # the end of one mapping and into whatever the host put next to it.
        if host is None or self.host_address(logical + size - 1) != host + size - 1:
            return None
        return self._read_raw(host, size)
