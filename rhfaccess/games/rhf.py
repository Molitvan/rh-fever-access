"""Rhythm Heaven Fever specifics.

Nothing in here is guessed. Addresses live in WATCHES and start empty; each one
gets added only after it has been found with `tools/scan.py` and confirmed to
survive a reboot of the game. Until a structure is verified, the companion
simply does not talk about it.

Scope note: RHF is a rhythm game, so the beat itself is already audio. The
accessibility gap is everything *around* the beat — the title flow, the game
grid, which game the cursor is on, whether it is locked, the practice/skip
prompts, and the rank you were just given. That is what this module targets
first.
"""

from __future__ import annotations

import time

from typing import Callable, Dict, Hashable, Iterable, List, Optional

from ..probes import Probe, Utterance
from . import archive, panes

# Disc IDs, to be confirmed against whatever `game_id()` actually reports.
GAME_IDS: Dict[str, str] = {
    "SOME01": "Rhythm Heaven Fever (US)",
    "SOMP01": "Beat the Beat: Rhythm Paradise (EU)",
    "SOMJ01": "Minna no Rhythm Tengoku (JP)",
    "SOMK01": "Rhythm World Wii (KR)",
}


def is_rhythm_heaven(game_id: Optional[str]) -> bool:
    return bool(game_id) and game_id in GAME_IDS


class GameIdentityProbe(Probe):
    """Announces what Dolphin is actually running.

    This is the one thing we can read with total confidence today: the disc
    header sits at a fixed address on every Wii title. It doubles as the
    sanity check that the hook is live and pointed at the right game.
    """

    name = "identity"
    interval = 0.5
    stable_ticks = 2

    def read(self, link) -> Optional[Hashable]:
        game_id = link.game_id()
        if game_id is None:
            return None
        return (game_id, link.game_title())

    def describe(self, previous, current) -> Iterable[Utterance]:
        game_id, title = current
        known = GAME_IDS.get(game_id)
        if known:
            return [Utterance(f"{known} detected.", interrupt=True, priority=10)]
        label = title or game_id
        return [Utterance(
            f"Connected, but this is {label}, not Rhythm Heaven Fever. "
            "Game-specific announcements are off.",
            interrupt=True, priority=10)]


class Watch:
    """A single verified memory location, described declaratively.

    Once scan.py pins an address down, wiring it up is one entry:

        Watch(
            key="grid_cursor",
            address=0x8xxxxxxx,
            kind="u8",
            valid=lambda v: v < 50,
            label=lambda v: GAME_NAMES.get(v),
            interrupt=True,
        )

    `valid` is the corroboration step: a value outside the plausible range
    means the structure is not live right now, which the engine treats as
    unverified rather than as news.
    """

    def __init__(
        self,
        key: str,
        address: int,
        kind: str = "u8",
        offsets: Iterable[int] = (),
        valid: Optional[Callable[[object], bool]] = None,
        label: Optional[Callable[[object], Optional[str]]] = None,
        interrupt: bool = True,
        priority: int = 0,
        gate: Optional[Callable[[object], bool]] = None,
    ) -> None:
        self.key = key
        self.address = address
        self.kind = kind
        self.offsets = tuple(offsets)
        self.valid = valid
        self.label = label
        self.interrupt = interrupt
        self.priority = priority
        self.gate = gate  # extra context check, e.g. "only while in a menu"


class WatchProbe(Probe):
    """Reads one Watch and speaks its label when the value changes."""

    stable_ticks = 2

    def __init__(self, watch: Watch, interval: float = 0.0) -> None:
        self.watch = watch
        self.name = watch.key
        self.interval = interval

    def read(self, link) -> Optional[Hashable]:
        w = self.watch
        if w.gate is not None and not w.gate(link):
            return None

        address = w.address
        if w.offsets:
            address = link.chase(w.address, *w.offsets)
            if address is None:
                return None

        reader = getattr(link, w.kind, None)
        if reader is None:
            return None
        value = reader(address)
        if value is None:
            return None
        if w.valid is not None and not w.valid(value):
            return None
        return value

    def describe(self, previous, current) -> Iterable[Utterance]:
        w = self.watch
        text = w.label(current) if w.label else f"{w.key} {current}"
        if not text:
            return ()
        return [Utterance(text, interrupt=w.interrupt, priority=w.priority)]


# Verified addresses go here. See the RE workflow in README.
WATCHES: List[Watch] = []


# -- game select ---------------------------------------------------------

# Cursor position on the game-select screen. Found by automated return-to-origin
# scanning; the byte is stored twice (0x80320404 and 0x80320405), which is why a
# u32 read of it looks like 0x06060000. 0xFF means "no valid selection".
ADDR_GRID_INDEX = 0x80320404

# Pointer to the selected entry's display object, in an array of 0x50-byte
# structs on the MEM2 heap. Used to corroborate the index: the two must stay
# consistent, or we are not looking at the game grid.
ADDR_GRID_ENTRY_PTR = 0x80320430
ENTRY_STRIDE = 0x50

# Which part of the game-select screen is up. Found by driving Z/X (select and
# back) through the automated scanner: it returns to 1 every time the card
# closes and reads 3 for as long as it is open.
ADDR_MENU_STATE = 0x8032A5C0
MENU_STATE_GRID = 1
MENU_STATE_CARD = 3

# Text panes on the info card that appears when a game is selected.
PANE_CARD_TITLE = "T_game_title_00"
PANE_CARD_TEXT = "T_exposition_00"

# --- save file select (the first screen after the title) ----------------
#
# Four slots in a 2x2 grid; the index walks 0,1 across the top and 2,3 along
# the bottom. This is a MEM2 heap address, but the game's allocator is
# deterministic: it landed here again, byte for byte, after a full reboot.
# Validated as 0-3 on every read regardless.
ADDR_FILE_SLOT = 0x90DEBB71
FILE_SLOT_COUNT = 4

PANE_FILE_PROMPT = "T_no_data_00"     # "Select one!"
PANE_FILE_FLOW = "T_nori_num_0{}"     # "nori" = groove; the Flow number
PANE_FILE_MEDALS = "T_medal_num_0{}"

INVALID_INDEX = 0xFF
MAX_NAME_LENGTH = 32

# The cursor walks the tower one entry at a time: four games, then that row's
# remix, then the next row. The text archive does not store them that way — it
# keeps every game in one consecutive run and every remix in another, far
# earlier. Translating between the two is the whole job here.
#
# A plain "archive index = cursor index + 99" appears to work on row 1 purely
# because the two runs happen to line up there. It breaks on the first remix
# (announcing "Fork Lifter" for Remix 1) and then slips one further per row.
GRID_FIRST_INDEX = 5        # cursor index of the bottom entry (Hole in One)
ROW_LENGTH = 5              # four games and a remix
GAMES_PER_ROW = 4
TOTAL_ROWS = 10             # 40 games + 10 remixes = 50 entries

# Archive positions, read off the US build's table (SOME01).
ARCHIVE_FIRST_GAME = 104    # 104..143: Hole in One .. Karate Man 2
ARCHIVE_FIRST_REMIX = 55    # 55..64:   Remix 1 .. Remix 10


# Cursor indices 0-4 are the extras column, in the same array as the games.
# The archive does hold names for these, but they are internal scene names
# ("Rhythm Toy Menu", "Café") rather than the labels the game prints on screen
# ("Rhythm Toys", "Rhythm Café") — unlike the games, where the archive text and
# the on-screen banner match exactly. These three were read off the banner.
# Indices 3 and 4 are the locked "?" entries and stay unnamed.
EXTRAS = {
    0: "Rhythm Café",
    1: "Rhythm Toys",
    2: "Endless Games",
}


def archive_index_for(cursor_index: int) -> Optional[int]:
    """Map a game-select cursor position to its name in the text archive."""
    slot = cursor_index - GRID_FIRST_INDEX
    if slot < 0:
        return None
    row, position = divmod(slot, ROW_LENGTH)
    if row >= TOTAL_ROWS:
        return None
    if position < GAMES_PER_ROW:
        return ARCHIVE_FIRST_GAME + row * GAMES_PER_ROW + position
    return ARCHIVE_FIRST_REMIX + row


def label_for(cursor_index: int, text_archive) -> Optional[str]:
    """The name to speak for a cursor position, or None to stay silent."""
    if cursor_index in EXTRAS:
        return EXTRAS[cursor_index]
    name_index = archive_index_for(cursor_index)
    if name_index is None or text_archive is None:
        return None
    label = text_archive.text(name_index)
    if not label:
        return None
    label = label.strip()
    if len(label) > MAX_NAME_LENGTH or "\n" in label or not label.isprintable():
        return None
    return label


class GridCursorProbe(Probe):
    """Speaks the game-select entry under the cursor.

    Three things must agree before this says anything: the index must be in
    range, the entry pointer must land in the MEM2 heap, and the archive lookup
    must produce a short, single-line, printable label. Menu labels are short;
    if a lookup returns a paragraph, we are reading the wrong thing and the
    probe stays quiet.
    """

    name = "grid_cursor"
    interval = 0.05
    stable_ticks = 2

    def __init__(self) -> None:
        self._archive = None
        self._entry_base = None

    def reset(self) -> None:
        self._archive = None
        self._entry_base = None

    def _text_archive(self, link):
        if self._archive is not None and self._archive.still_valid():
            return self._archive
        self._archive = archive.find(link)
        return self._archive

    def read(self, link) -> Optional[Hashable]:
        if link.u32(ADDR_MENU_STATE) != MENU_STATE_GRID:
            return None
        index = link.u8(ADDR_GRID_INDEX)
        if index is None or index == INVALID_INDEX:
            return None
        # The byte is mirrored; if the copies disagree we caught a partial write.
        if link.u8(ADDR_GRID_INDEX + 1) != index:
            return None
        entry = link.pointer(ADDR_GRID_ENTRY_PTR)
        if entry is None:
            return None

        # Entries sit in one array, so the selected pointer and the index must
        # agree: entry == base + index * stride. The base is whatever the heap
        # handed out this run, but it has to stay put. When the cursor leaves
        # the grid the pointer jumps to a different array and this stops
        # holding, which is exactly when we want to go quiet.
        base = entry - index * ENTRY_STRIDE
        if self._entry_base != base:
            self._entry_base = base
            return None

        label = label_for(index, self._text_archive(link))
        if label is None:
            return None
        return (index, label)

    def describe(self, previous, current) -> Iterable[Utterance]:
        _index, label = current
        return [Utterance(label, interrupt=True, priority=5)]


class InfoCardProbe(Probe):
    """Speaks the card shown after picking a game: its title and description.

    The panes keep their last string after the card closes, so this is gated on
    the menu state rather than on the text itself — otherwise it would announce
    a stale description every time the grid redrew.
    """

    name = "info_card"
    interval = 0.1
    stable_ticks = 2

    def __init__(self) -> None:
        self._panes = None

    def reset(self) -> None:
        self._panes = None

    def read(self, link) -> Optional[Hashable]:
        if link.u32(ADDR_MENU_STATE) != MENU_STATE_CARD:
            return None
        if self._panes is None:
            self._panes = panes.PaneIndex(link)
        wanted = (PANE_CARD_TITLE, PANE_CARD_TEXT)
        if not self._panes.ensure(wanted):
            return None
        title = self._panes.text(PANE_CARD_TITLE)
        description = self._panes.text(PANE_CARD_TEXT)
        if not title or not description:
            return None
        return (title, description)

    def describe(self, previous, current) -> Iterable[Utterance]:
        title, description = current
        return [Utterance(f"{title}. {description}", interrupt=True, priority=8)]


# The title screen carries no text at all — the prompt is a picture of a Wii
# Remote with A and B lit up, and a pane sweep there returns zero text panes.
# So unlike everywhere else in this file, this string is authored rather than
# read from the game. It is the one place where staying silent would leave a
# blind player with no way to know what to press.
TITLE_ANNOUNCEMENT = ("Rhythm Heaven Fever, title screen. "
                      "Hold the Wii Remote sideways and press A and B together "
                      "to continue.")


class TitleScreenProbe(Probe):
    """Announces the title screen, detected by it having no text panes at all.

    Every other screen seen so far exposes at least twenty. The check is a full
    MEM2 sweep, so it is rate-limited and switches itself off permanently as
    soon as any other screen appears — it only ever runs during the few seconds
    the title screen is up.

    UNVERIFIED: the boot logos presumably also have no text panes. If this
    announces too early, gate it on something else.
    """

    name = "title_screen"
    interval = 1.0
    stable_ticks = 1

    def __init__(self) -> None:
        self._panes = None
        self._done = False
        self._next_scan = 0.0

    def reset(self) -> None:
        self._panes = None
        self._done = False
        self._next_scan = 0.0

    def read(self, link) -> Optional[Hashable]:
        if self._done:
            return None
        if link.u8(ADDR_GRID_INDEX) != INVALID_INDEX:
            self._done = True
            return None

        now = time.monotonic()
        if now < self._next_scan:
            return None
        self._next_scan = now + 2.0

        if self._panes is None:
            self._panes = panes.PaneIndex(link)
        if self._panes.scan():
            self._done = True      # some other screen is up; stop sweeping
            return None
        return ("title",)

    def describe(self, previous, current) -> Iterable[Utterance]:
        return [Utterance(TITLE_ANNOUNCEMENT, interrupt=True, priority=9)]


class FileSelectProbe(Probe):
    """Speaks the highlighted save slot on the file select screen.

    Slot contents come from the game: a slot with a save owns Flow and Medals
    panes, and a slot without them is an empty "New Game" box. The empty label
    is ours — the game draws those words as artwork, not text.

    Gated on the game grid being inactive, so it stays quiet once play starts.
    """

    name = "file_select"
    interval = 0.1
    stable_ticks = 2

    def __init__(self) -> None:
        self._panes = None
        self._scanned = False

    def reset(self) -> None:
        self._panes = None
        self._scanned = False

    def read(self, link) -> Optional[Hashable]:
        # A valid grid index means we are in the game tower, not the file list.
        if link.u8(ADDR_GRID_INDEX) != INVALID_INDEX:
            return None
        slot = link.u8(ADDR_FILE_SLOT)
        if slot is None or slot >= FILE_SLOT_COUNT:
            return None

        if self._panes is None:
            self._panes = panes.PaneIndex(link)
        # One full sweep caches every slot's panes at once. Asking only for the
        # ones we want would rescan forever on empty slots, whose Flow and
        # Medals panes legitimately do not exist.
        if not self._panes.ensure([PANE_FILE_PROMPT]):
            return None
        if not self._scanned:
            self._panes.scan()
            self._scanned = True

        flow = self._panes.text(PANE_FILE_FLOW.format(slot))
        medals = self._panes.text(PANE_FILE_MEDALS.format(slot))
        if flow and medals:
            return (slot, f"File {slot + 1}. Flow {flow}. {medals} medals.")
        return (slot, f"File {slot + 1}. New game.")

    def describe(self, previous, current) -> Iterable[Utterance]:
        _slot, text = current
        return [Utterance(text, interrupt=True, priority=6)]


def build_probes() -> List[Probe]:
    probes: List[Probe] = [GameIdentityProbe(), TitleScreenProbe(), GridCursorProbe(),
                           InfoCardProbe(),
                           FileSelectProbe()]
    probes.extend(WatchProbe(w) for w in WATCHES)
    return probes
