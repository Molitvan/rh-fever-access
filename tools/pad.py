"""Synthetic keyboard input for driving Dolphin during a scan.

Dolphin's keyboard device here is `DInput/0/Keyboard Mouse`, and DirectInput
reads *scancodes*, not virtual key codes — so everything below is sent with
KEYEVENTF_SCANCODE or Dolphin will not see it.

This exists so a scan can press the button itself. Doing fifty press-and-sample
cycles by hand is not realistic; doing them automatically turns a statistical
argument into a decisive one.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Optional

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9
VK_MENU = 0x12

# Set-1 scancodes for the keys in WiimoteNew.ini.
SCANCODES = {
    "W": 0x11, "A": 0x1E, "S": 0x1F, "D": 0x20,
    "Z": 0x2C, "X": 0x2D, "Q": 0x10, "E": 0x12,
    "1": 0x02, "2": 0x03, "RETURN": 0x1C, "SPACE": 0x39,
    "UP": 0x48, "DOWN": 0x50, "LEFT": 0x4B, "RIGHT": 0x4D,
}

EXTENDED = {"UP", "DOWN", "LEFT", "RIGHT"}
KEYEVENTF_EXTENDEDKEY = 0x0001


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _send(scan: int, keyup: bool, extended: bool) -> None:
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if keyup else 0)
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp = INPUT(type=INPUT_KEYBOARD,
                u=_INPUTUNION(ki=KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags,
                                            time=0, dwExtraInfo=None)))
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def tap(key: str, hold: float = 0.08) -> None:
    """Press and release one key, holding long enough for the game to sample it."""
    key = key.upper()
    if key not in SCANCODES:
        raise ValueError(f"Unknown key {key!r}")
    scan = SCANCODES[key]
    extended = key in EXTENDED
    _send(scan, False, extended)
    time.sleep(hold)
    _send(scan, True, extended)


def find_dolphin() -> Optional[int]:
    """Return the HWND of Dolphin's main (render) window."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value.startswith("Dolphin"):
            found.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return found[0] if found else None


def focus(hwnd: int) -> bool:
    """Bring Dolphin forward. Windows blocks a plain SetForegroundWindow from a
    background process, so nudge ALT first — the documented way to get the
    foreground lock released."""
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, 2, 0)
    ok = bool(user32.SetForegroundWindow(hwnd))
    time.sleep(0.15)
    return ok


def client_rect(hwnd: int):
    """Screen coordinates of the window's client area (the rendered game)."""
    class RECT(ctypes.Structure):
        _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                    ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    rect = RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return (origin.x, origin.y,
            origin.x + rect.right, origin.y + rect.bottom)


def screenshot(hwnd: int, path: str, crop=None) -> bool:
    """Save the game's client area to a PNG. Dolphin must be visible.

    crop is an optional (x0, y0, x1, y1) box in *fractions* of the client area,
    so it survives a window resize. Cropping to the name banner keeps the files
    small enough to read a whole sweep of them.
    """
    try:
        from PIL import ImageGrab
    except ImportError:
        return False
    box = client_rect(hwnd)
    if box[2] <= box[0] or box[3] <= box[1]:
        return False
    image = ImageGrab.grab(bbox=box, all_screens=True)
    if crop:
        w, h = image.size
        image = image.crop((int(crop[0] * w), int(crop[1] * h),
                            int(crop[2] * w), int(crop[3] * h)))
    image.save(path)
    return True


def grab() -> Optional[int]:
    """Find and focus Dolphin. Returns the HWND, or None if it is not running."""
    hwnd = find_dolphin()
    if hwnd is None:
        return None
    focus(hwnd)
    return hwnd
