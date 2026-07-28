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


def find_process(name: str = "Dolphin.exe") -> Optional[int]:
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return None
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snap, ctypes.byref(entry)):
            return None
        while True:
            if entry.szExeFile.lower() == name.lower():
                return int(entry.th32ProcessID)
            if not k32.Process32NextW(snap, ctypes.byref(entry)):
                return None
    finally:
        k32.CloseHandle(snap)


class RawMemory:
    """Read-only view of Dolphin's emulated MEM1/MEM2 via ReadProcessMemory."""

    def __init__(self) -> None:
        self.pid: Optional[int] = None
        self._handle = None
        self.mem1_base: Optional[int] = None
        self.mem2_base: Optional[int] = None

    # -- setup -----------------------------------------------------------

    def attach(self, game_id: Optional[bytes] = None) -> bool:
        self.close()
        pid = find_process()
        if pid is None:
            return False
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
            if not head or not head.isalnum():
                continue
            if game_id is not None and head != game_id:
                continue
            for offset in MEM2_OFFSETS:
                candidate = base + offset
                if sizes.get(candidate) in MEM2_SIZES:
                    self.mem1_base, self.mem2_base = base, candidate
                    return True

        self.close()
        return False

    def close(self) -> None:
        if self._handle:
            k32.CloseHandle(self._handle)
        self._handle = None
        self.pid = self.mem1_base = self.mem2_base = None

    @property
    def available(self) -> bool:
        return self._handle is not None and self.mem2_base is not None

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
        if self.mem2_base is not None and MEM2_LOGICAL <= logical < MEM2_LOGICAL + 0x4000000:
            return self.mem2_base + (logical - MEM2_LOGICAL)
        if self.mem1_base is not None and MEM1_LOGICAL <= logical < MEM1_LOGICAL + 0x1800000:
            return self.mem1_base + (logical - MEM1_LOGICAL)
        return None

    def read(self, logical: int, size: int) -> Optional[bytes]:
        if not self.available or size <= 0:
            return None
        host = self.host_address(logical)
        if host is None:
            return None
        return self._read_raw(host, size)
