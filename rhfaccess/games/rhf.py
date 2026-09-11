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

import re
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

# DO NOT gate on this. It looked like a screen ID when found by driving Z/X
# through the scanner — 1 on the grid, 3 with the card open — but a live trace
# while scrolling showed it reading 3 the whole time. Whatever it tracks, it is
# not which screen is up. Gating on it made the grid go silent and the card
# announce on every cursor move. Kept only so nobody rediscovers it and repeats
# the mistake.
ADDR_MENU_STATE = 0x8032A5C0

# The grid index is the real discriminator between the two: it holds a valid
# entry number while you move around the tower, and 0xFF once the cursor is
# handed off to the card (or to a game).

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

# New saves pass through a second file-related screen whose fifteen labels are
# artwork rather than text. The cursor is an NW4R pane with a live transform;
# its position exactly matches the container pane of the highlighted label.
PANE_SAVE_LABEL_PROMPT = "T_menu_msg_01"
PANE_SAVE_LABEL_CURSOR = "N_cursor_frm_00"
PANE_SAVE_LABEL_GROUP = "W_menu_01"
PANE_SAVE_LABEL_BACK = "T_back_btn_02"
PANE_SAVE_LABEL_MII = "T_Mii_btn_00"
PANE_SAVE_CONFIRM_PROMPT = "T_msg_02"
PANE_SAVE_CONFIRM_NO = "T_NG_btn_00"
PANE_SAVE_CONFIRM_YES = "T_OK_btn_00"

# Row-major order, read from the US screen. Like the tower's EXTRAS, these have
# to be authored because the game renders the words as artwork.
SAVE_LABELS = (
    "Me", "Friend", "Pal", "Mom", "Dad",
    "Myself", "Grandma", "Grandpa", "B.F.", "G.F.",
    "You", "Big Bro", "Big Sis", "Li'l Bro", "Li'l Sis",
)

# Live transforms of N_name_00..14. The cursor at Dad was verified as
# (124.231, 83.195), exactly N_name_04's transform. The two button positions
# come from N_back_btn_02 and N_mii_btn_00 in the same live layout.
SAVE_LABEL_X = (-124.23075, -62.11538, 0.0, 62.11538, 124.23075)
SAVE_LABEL_Y = (83.19475, -1.80525, -86.80525)
SAVE_LABEL_BUTTONS = {
    (-94.26923, -174.0): PANE_SAVE_LABEL_BACK,
    (60.65385, -174.0): PANE_SAVE_LABEL_MII,
}
SAVE_LABEL_POSITION_TOLERANCE = 2.0
SAVE_LABEL_GROUP_ONSCREEN_Y = -34.0
SAVE_CONFIRM_BUTTONS = {
    (-95.0, -126.0): PANE_SAVE_CONFIRM_NO,
    (95.0, -126.0): PANE_SAVE_CONFIRM_YES,
}

INVALID_INDEX = 0xFF
MAX_NAME_LENGTH = 32


class _SaveLabelCursor:
    """Locate and decode the save-label cursor without caching a heap address.

    The name exists twice: once in the live pane and once in the serialized
    layout data. A usable alpha plus a coordinate matching a selectable target
    separates the live object. Re-scan after every raw-memory generation.
    """

    def __init__(self) -> None:
        self._address: Optional[int] = None
        self._group_address: Optional[int] = None
        self._generation = -1

    @staticmethod
    def _choice_at(x: float, y: float) -> Optional[Hashable]:
        for row, target_y in enumerate(SAVE_LABEL_Y):
            for column, target_x in enumerate(SAVE_LABEL_X):
                if (abs(x - target_x) <= SAVE_LABEL_POSITION_TOLERANCE
                        and abs(y - target_y) <= SAVE_LABEL_POSITION_TOLERANCE):
                    return row * len(SAVE_LABEL_X) + column
        for (target_x, target_y), pane in SAVE_LABEL_BUTTONS.items():
            if (abs(x - target_x) <= SAVE_LABEL_POSITION_TOLERANCE
                    and abs(y - target_y) <= SAVE_LABEL_POSITION_TOLERANCE):
                return pane
        return None

    def _position(self, link, address: int) -> Optional[tuple]:
        """Return the live cursor position, including while it is animating."""
        if link.cstring(address, MAX_PANE_NAME, "ascii") != PANE_SAVE_LABEL_CURSOR:
            return None
        alpha = link.u8(address + panes.ALPHA_OFFSET)
        x = link.f32(address - 0x2C)
        y = link.f32(address - 0x1C)
        if alpha is None or alpha < panes.VISIBLE_ALPHA or x is None or y is None:
            return None
        # The serialized layout copy decodes as enormous/non-finite coordinates
        # and has alpha 4. These generous bounds retain the live pane while it
        # travels between choices without accepting that resource copy.
        if not (-1000.0 <= x <= 1000.0 and -1000.0 <= y <= 1000.0):
            return None
        return (x, y)

    def _locate(self, link) -> Optional[int]:
        generation = getattr(link, "generation", 0)
        if generation != self._generation:
            self._generation = generation
            self._address = None
            self._group_address = None

        if self._address is not None and self._position(link, self._address) is not None:
            return self._address
        if self._address is not None:
            self._address = None

        target = PANE_SAVE_LABEL_CURSOR.encode("ascii") + b"\x00"
        matches = []
        address = panes.MEM2_START
        end = panes.MEM2_START + min(panes.MEM2_SIZE, link.mem2_extent())
        tail = b""
        tail_address = address
        complete = True

        while address < end:
            block = link.read(address, min(panes.CHUNK, end - address))
            if not block:
                complete = False
                address += panes.CHUNK
                tail = b""
                continue
            data = tail + block
            base = tail_address if tail else address
            offset = data.find(target)
            while offset != -1:
                hit = base + offset
                if hit % 4 == 0 and self._position(link, hit) is not None:
                    matches.append(hit)
                offset = data.find(target, offset + 1)
            overlap = len(target) - 1
            tail = block[-overlap:]
            tail_address = address + len(block) - overlap
            address += panes.CHUNK

        if not complete or len(matches) != 1:
            return None
        self._address = matches[0]
        return self._address

    def _group_position(self, link, address: int) -> Optional[tuple]:
        if link.cstring(address, MAX_PANE_NAME, "ascii") != PANE_SAVE_LABEL_GROUP:
            return None
        # Runtime panes have the NW4R type byte immediately before their final
        # flag byte. Serialized layout records with the same name do not.
        if link.u8(address - 2) != 0x04:
            return None
        x = link.f32(address - 0x2C)
        y = link.f32(address - 0x1C)
        if x is None or y is None:
            return None
        if not (-1000.0 <= x <= 1000.0 and -1000.0 <= y <= 1000.0):
            return None
        return (x, y)

    def _locate_group(self, link) -> Optional[int]:
        if (self._group_address is not None
                and self._group_position(link, self._group_address) is not None):
            return self._group_address
        self._group_address = None

        target = PANE_SAVE_LABEL_GROUP.encode("ascii") + b"\x00"
        matches = []
        address = panes.MEM2_START
        end = panes.MEM2_START + min(panes.MEM2_SIZE, link.mem2_extent())
        tail = b""
        tail_address = address
        complete = True
        while address < end:
            block = link.read(address, min(panes.CHUNK, end - address))
            if not block:
                complete = False
                address += panes.CHUNK
                tail = b""
                continue
            data = tail + block
            base = tail_address if tail else address
            offset = data.find(target)
            while offset != -1:
                hit = base + offset
                if hit % 4 == 0 and self._group_position(link, hit) is not None:
                    matches.append(hit)
                offset = data.find(target, offset + 1)
            overlap = len(target) - 1
            tail = block[-overlap:]
            tail_address = address + len(block) - overlap
            address += panes.CHUNK

        if not complete or len(matches) != 1:
            return None
        self._group_address = matches[0]
        return self._group_address

    def present(self, link) -> bool:
        """True while the label group is on screen, including between choices."""
        if self._locate(link) is None:
            return False
        group = self._locate_group(link)
        if group is None:
            return False
        position = self._group_position(link, group)
        if position is None:
            return False
        x, y = position
        # When file select is active this whole submenu is parked at Y=-500,
        # even though its children retain their text, alpha and cursor state.
        # Its measured resting transform on the label screen is Y=-34.
        return (abs(x) <= SAVE_LABEL_POSITION_TOLERANCE
                and abs(y - SAVE_LABEL_GROUP_ONSCREEN_Y) <= SAVE_LABEL_POSITION_TOLERANCE)

    def selection(self, link) -> Optional[Hashable]:
        if not self.present(link):
            return None
        address = self._locate(link)
        if address is None:
            return None
        position = self._position(link, address)
        if position is None:
            return None
        return self._choice_at(*position)

    def confirmation_selection(self, link) -> Optional[str]:
        """Text pane for the highlighted No/Yes button, or None."""
        if not self.present(link):
            return None
        address = self._locate(link)
        if address is None:
            return None
        position = self._position(link, address)
        if position is None:
            return None
        x, y = position
        for (target_x, target_y), pane in SAVE_CONFIRM_BUTTONS.items():
            if (abs(x - target_x) <= SAVE_LABEL_POSITION_TOLERANCE
                    and abs(y - target_y) <= SAVE_LABEL_POSITION_TOLERANCE):
                return pane
        return None


# Everything selectable in the game menu — the tower entries and the row of
# buttons beside it — is backed by an NW4R pane, and ADDR_GRID_ENTRY_PTR points
# at the selected one's object whichever kind it is. The object holds a pointer
# to its pane at +0x04, and the pane carries its own ASCII name at +0xBC.
#
# This is what makes the buttons readable. The cursor index only covers the
# tower, and reads 0xFF the moment the cursor steps off it, so there is no
# index to look a button up by; the pointer, however, still moves.
SELECTION_PANE_OFFSET = 0x04
PANE_NAME_OFFSET = 0xBC
MAX_PANE_NAME = 32

# Container panes are "N_...", their text panes "T_..." — the button labelled
# "Two Player" is N_2play_btn_00 holding T_2play_btn_00.
CONTAINER_PANE = re.compile(r"^N_([A-Za-z0-9_]{2,28})$")

# The tower's own entries are panes too (N_game_btn_13), the three extras
# included. None of them has a matching text pane, because the game draws those
# names as artwork — which is the reason EXTRAS is hardcoded. Asking for one
# anyway would sweep MEM2 every few seconds looking for something that cannot
# exist, so they are recognised and skipped rather than merely failing.
TOWER_PANE = re.compile(r"^N_game_btn_\d+$")


def selected_pane_name(link) -> Optional[str]:
    """ASCII name of the pane behind the current menu selection, or None."""
    entry = link.pointer(ADDR_GRID_ENTRY_PTR)
    if entry is None:
        return None
    pane = link.pointer(entry + SELECTION_PANE_OFFSET)
    if pane is None:
        return None
    raw = link.read(pane + PANE_NAME_OFFSET, MAX_PANE_NAME)
    if not raw:
        return None
    end = raw.find(b"\x00")
    if end <= 0:
        return None
    try:
        return raw[:end].decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return None


def selected_button_pane(link) -> Optional[str]:
    """Text pane holding the label of the selected *button*, or None.

    None whenever the selection is a tower entry, which is what keeps this
    apart from the info card: the card sits behind the same 0xFF index with the
    pointer still on the game you picked.
    """
    name = selected_pane_name(link)
    if name is None or TOWER_PANE.match(name):
        return None
    match = CONTAINER_PANE.match(name)
    if match is None:
        return None
    return "T_" + match.group(1)


def no_selection(link) -> bool:
    """True when nothing on the game grid is or was being played.

    Needed because "no text panes on screen" describes the title screen *and*
    a game in progress, so it cannot tell them apart on its own. The grid
    state can:

      * before the menu has ever been built, the entry pointer is null —
        this is the cold boot, where the index byte reads 0x00 rather than
        0xFF and so looks like a real selection;
      * after backing out of the menu, the pointer holds the game's
        no-selection sentinel and the committed-selection byte reads 0xFF;
      * during a game, both still refer to the entry you launched.

    So the first two are the title screen and the third is not.
    """
    entry = link.pointer(ADDR_GRID_ENTRY_PTR)
    if entry is None:
        return True
    return link.u8(ADDR_GRID_INDEX + 1) == INVALID_INDEX

# Third pane on the info card: the game's own control hint, e.g.
# "A: Play!  B: Go back." Preferred over writing our own.
PANE_CARD_CONTROLS = "T_comment_00"

# Orientation lines, spoken once on arriving at a screen and then not repeated
# while moving around inside it. Audio games do this because a screen reader
# user gets no free glance at the layout: without it you hear "File 2" with no
# idea what a file is or how to act on it.
#
# These describe *what the buttons do*, which the game only conveys visually,
# so they are authored. Item names and anything the game states in text are
# still read from the game.
INTRO_FILE_SELECT = ("File menu. Use the D-pad to move between save files, "
                     "and press A to select one.")
INTRO_GAME_GRID = ("Game menu. Up and down move through the games in a set. "
                   "Left and right jump between sets. Press A to select a game.")


class ScreenTracker:
    """Remembers which screen is up, so intros play on arrival only.

    Shared by the screen probes. Returning to the grid from a game's info card
    is not a fresh arrival — you never left the menu — so the grid intro does
    not replay every time you look at a game and back out.
    """

    # Both the info card and the post-game epilogue sit behind a 0xFF grid
    # index, and both keep live panes, so neither can be told from the other by
    # reading text. What separates them is timing: a card is opened straight
    # off the grid, while an epilogue only arrives after a game has been played
    # for a while. These windows encode that.
    CARD_MAX_GAP = 3.0      # a card follows the grid almost immediately
    RESULT_MIN_GAP = 5.0    # an epilogue cannot; a game takes longer than this

    def __init__(self) -> None:
        self.current: Optional[str] = None
        self.visits: Dict[str, int] = {}
        self.last_grid_seen = 0.0
        self._panes = None
        self._save_label_cursor = _SaveLabelCursor()

    def pane_index(self, link):
        """The one pane index, shared by every probe that reads text.

        This must not be per-probe. A sweep costs ~0.14s, and a probe sweeps
        whenever a pane it wants is absent — which is most of the time, since
        each screen only owns a few. With one index each, four probes swept
        independently and single poll ticks took over 800ms, which is what made
        speech lag behind the screen. Sharing means one sweep populates
        everything at once.
        """
        if self._panes is None:
            self._panes = panes.PaneIndex(link, rescan_interval=5.0)
        return self._panes

    def note_grid(self, active: bool) -> None:
        if active:
            self.last_grid_seen = time.monotonic()

    def since_grid(self) -> float:
        if not self.last_grid_seen:
            return float("inf")
        return time.monotonic() - self.last_grid_seen

    def save_label_selection(self, link) -> Optional[Hashable]:
        return self._save_label_cursor.selection(link)

    def save_label_present(self, link) -> bool:
        return self._save_label_cursor.present(link)

    def save_confirmation_selection(self, link) -> Optional[str]:
        return self._save_label_cursor.confirmation_selection(link)

    def enter(self, name: str, quiet_from: Iterable[str] = ()) -> int:
        """Mark `name` active; returns that screen's own arrival count.

        Counts are per screen. A single shared counter does not work: opening a
        card would bump it, and the grid would then see a changed value when you
        came back and replay its intro.
        """
        if self.current != name:
            if self.current not in tuple(quiet_from):
                self.visits[name] = self.visits.get(name, 0) + 1
            self.current = name
        return self.visits.get(name, 0)


class _IntroState:
    """Tracks whether this screen's orientation line is still owed."""

    def __init__(self) -> None:
        self.pending = False
        self._visit = -1

    def update(self, visit: int) -> None:
        if visit != self._visit:
            self._visit = visit
            self.pending = True

    def take(self) -> bool:
        was, self.pending = self.pending, False
        return was


def _with_intro(intro: Optional[str], item: str, priority: int) -> List[Utterance]:
    """Intro first, then the item queued behind it so it is not cut off."""
    if not intro:
        return [Utterance(item, interrupt=True, priority=priority)]
    return [
        Utterance(intro, interrupt=True, priority=priority + 1),
        Utterance(item, interrupt=False, priority=priority),
    ]

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


def set_of(cursor_index: int) -> Optional[int]:
    """1-based set (row) number for a cursor position, or None for the extras."""
    slot = cursor_index - GRID_FIRST_INDEX
    if slot < 0:
        return None
    row = slot // ROW_LENGTH
    return row + 1 if row < TOTAL_ROWS else None


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
    # Screens flicker while a card animates open or shut. Confirming over
    # 0.2s and remembering for a full second rides that out, without being
    # slow enough to notice when actually moving the cursor.
    stable_ticks = 4
    forget_after = 20

    # Locating the archive is a full MEM2 sweep. A successful one is cached and
    # costs nothing thereafter, but a failed one caches nothing, so without this
    # the sweep ran on every poll — twenty times a second — for as long as the
    # lookup kept failing. That is more than enough to stall the loop and put
    # speech seconds behind the screen.
    ARCHIVE_RETRY_SECONDS = 2.0

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._archive = None
        self._entry_base = None
        self._tracker = tracker
        self._intro = _IntroState()
        self._last_set = None
        self._next_archive_scan = 0.0

    def reset(self) -> None:
        self._archive = None
        self._entry_base = None
        self._intro = _IntroState()
        self._last_set = None
        self._next_archive_scan = 0.0

    def _text_archive(self, link):
        if self._archive is not None and self._archive.still_valid():
            return self._archive
        now = time.monotonic()
        if now < self._next_archive_scan:
            return None
        self._next_archive_scan = now + self.ARCHIVE_RETRY_SECONDS
        self._archive = archive.find(link)
        return self._archive

    def read(self, link) -> Optional[Hashable]:
        index = link.u8(ADDR_GRID_INDEX)
        entry = link.pointer(ADDR_GRID_ENTRY_PTR)
        # A valid-looking index is not enough to call the grid active, and the
        # tracker must not be told otherwise: on a cold boot this byte reads
        # 0x00 — a real entry number — while the menu does not exist and the
        # entry pointer is still null. Taken at face value that told the card
        # and epilogue probes the grid had just been on screen, seconds before
        # the title screen had even appeared.
        #
        # The byte at +1 is the committed selection, not a mirror of the
        # index. The two agree only while the cursor is on the tower, which is
        # exactly the agreement being tested for here.
        if (index is None or index == INVALID_INDEX or entry is None
                or link.u8(ADDR_GRID_INDEX + 1) != index):
            self._tracker.note_grid(False)
            return None
        self._tracker.note_grid(True)

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

        # Coming back from a game's info card or from the button row beside the
        # tower is not a fresh arrival — you never left the game menu, so the
        # orientation line would only be in the way.
        visit = self._tracker.enter("grid", quiet_from=("card", "button"))
        self._intro.update(visit)
        return (visit, index, label, set_of(index))

    def describe(self, previous, current) -> Iterable[Utterance]:
        _visit, _index, label, current_set = current
        intro = INTRO_GAME_GRID if self._intro.take() else None

        # Left and right jump a whole set, and landing in a new set with only
        # the game's name spoken loses your place entirely. Say which set it is
        # — but only when it actually changed.
        #
        # Tracked here rather than read off `previous`, because `previous` is
        # None whenever the probe was idle long enough to forget: backing out
        # of a card would otherwise re-announce a set you never left.
        if current_set is not None and current_set != self._last_set:
            label = f"Set {current_set}. {label}"
        self._last_set = current_set

        return _with_intro(intro, label, priority=5)


class MenuButtonProbe(Probe):
    """Speaks the game menu's buttons: Two Player, Back, and the rest of the row.

    These are not on the tower, so the cursor index cannot name them — it reads
    0xFF for the whole row, the same value it reads for the info card and for a
    game in progress. What separates them is the selection pointer, which moves
    to the button's own object and from there to a pane whose name gives away
    which button it is.

    The label itself is the game's: N_2play_btn_00 -> T_2play_btn_00 -> "Two
    Player". Nothing is hardcoded, so this covers whatever else lives in that
    row and works in any region.
    """

    name = "menu_button"
    interval = 0.05
    # Same reasoning as the grid cursor: confirm over 0.2s so the row's open
    # and close animations cannot trigger it, and forget quickly enough that
    # coming back to a button announces it again.
    stable_ticks = 4
    forget_after = 20

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._panes = None
        self._tracker = tracker

    def reset(self) -> None:
        self._panes = None

    def read(self, link) -> Optional[Hashable]:
        # A live index means the cursor is on the tower, which belongs to
        # GridCursorProbe.
        if link.u8(ADDR_GRID_INDEX) != INVALID_INDEX:
            return None
        pane = selected_button_pane(link)
        if pane is None:
            return None
        if self._panes is None:
            self._panes = self._tracker.pane_index(link)
        self._panes.ensure([pane])
        text = self._panes.text(pane)
        if not text:
            return None
        self._tracker.enter("button", quiet_from=("grid",))
        return (pane, text)

    def describe(self, previous, current) -> Iterable[Utterance]:
        _pane, text = current
        return [Utterance(text, interrupt=True, priority=5)]


class InfoCardProbe(Probe):
    """Speaks the card shown after picking a game: its title and description.

    The panes keep their last string after the card closes, so this is gated on
    the menu state rather than on the text itself — otherwise it would announce
    a stale description every time the grid redrew.
    """

    name = "info_card"
    interval = 0.1
    # Confirm over 0.4s so the open/close animation cannot trigger it, but
    # forget quickly so reopening the same game's card speaks again.
    stable_ticks = 4
    forget_after = 8

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._panes = None
        self._tracker = tracker

    def reset(self) -> None:
        self._panes = None

    def read(self, link) -> Optional[Hashable]:
        # The card is up only once the grid has released the cursor. Do not be
        # tempted to detect it from the pane text instead: the game keeps the
        # card's title and description in step with the highlighted game while
        # you scroll, so those change constantly with no card on screen.
        if link.u8(ADDR_GRID_INDEX) != INVALID_INDEX:
            return None
        # Stepping off the tower onto the button row also drops the index to
        # 0xFF, straight from the grid, so the timing test below cannot rule it
        # out — and the card's panes still hold the last game you highlighted.
        # The selection pointer can rule it out: on a button it resolves to
        # that button's pane, and on a card it is still the game's entry.
        if selected_button_pane(link) is not None:
            return None
        # A card opens straight off the grid. If the grid has not been active
        # for seconds we are somewhere else behind a 0xFF index — in a game, or
        # on the epilogue — and the card's panes are merely stale.
        if self._tracker.since_grid() > ScreenTracker.CARD_MAX_GAP:
            return None
        if self._panes is None:
            self._panes = self._tracker.pane_index(link)
        # Look for the controls pane too, but do not require it: the title and
        # description are what must be there.
        self._panes.ensure((PANE_CARD_TITLE, PANE_CARD_TEXT, PANE_CARD_CONTROLS))
        title = self._panes.text(PANE_CARD_TITLE)
        description = self._panes.text(PANE_CARD_TEXT)
        if not title or not description:
            return None
        # The card states its own controls, so read them rather than invent any.
        controls = self._panes.text(PANE_CARD_CONTROLS)
        self._tracker.enter("card")
        return (title, description, controls)

    def describe(self, previous, current) -> Iterable[Utterance]:
        title, description, controls = current
        text = f"{title}. {description}"
        if controls:
            text = f"{text} {controls}"
        return [Utterance(text, interrupt=True, priority=8)]


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

    Every other menu screen exposes at least twenty — but a game in progress
    exposes none either, so the sweep alone cannot tell those two apart.
    `no_selection()` does: during a game the grid still points at the entry you
    launched, and on the title screen nothing is selected.

    This used to switch itself off for good the first time the grid index read
    anything but 0xFF. On a cold boot that byte reads 0x00, so it switched off
    during the Wii logos and the title screen was never announced at all — nor
    was it on any later visit, since the flag was only cleared by a Dolphin
    disconnect. Whatever replaces that flag has to survive going back to the
    title screen from the menu, which is a thing players do.

    The sweep is expensive, so it is rate-limited and only ever runs once the
    cheap check above has already passed — which it does not during a game or
    anywhere in the menu.

    UNVERIFIED: the boot logos have no text panes and no selection either, so
    this announces during them, a few seconds before the title screen is
    actually up. That is the pre-existing compromise, now reached sooner.
    """

    name = "title_screen"
    interval = 1.0
    stable_ticks = 1

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._panes = None
        self._tracker = tracker
        self._next_scan = 0.0

    def reset(self) -> None:
        self._panes = None
        self._next_scan = 0.0

    def read(self, link) -> Optional[Hashable]:
        if not no_selection(link):
            return None

        now = time.monotonic()
        if now < self._next_scan:
            return None
        self._next_scan = now + 2.0

        if self._panes is None:
            self._panes = self._tracker.pane_index(link)
        found = self._panes.scan()
        # None is not an empty screen — it is a sweep that could not read MEM2,
        # and this probe's whole signal is "no text panes anywhere". Treating
        # the two alike announced the title screen over the button row, the info
        # card, the file select and the cafe, on any session where the raw MEM2
        # backend had gone away, while everything that reads text went quiet.
        if found is None or found:
            return None
        self._tracker.enter("title")
        return ("title",)

    def describe(self, previous, current) -> Iterable[Utterance]:
        return [Utterance(TITLE_ANNOUNCEMENT, interrupt=True, priority=9)]


# The dialogue immediately after creating a save uses the same numbered names
# as gameplay tutorials, but occurs before the game menu exists: grid index 0,
# null entry pointer. Keep it separate so TutorialProbe's gameplay gate remains
# strict. This layout currently exposes only 00 and 01.
PANE_WELCOME_MESSAGES = ("T_message_00", "T_message_01")

# The low bit immediately before a numbered message pane's name tracks whether
# that pane is selected for display. Verified independently on the new-save
# welcome dialogue and Hole in One's tutorial layout.
MESSAGE_DISPLAY_FLAG_OFFSET = -1


class WelcomeDialogueProbe(Probe):
    """Read the introductory dialogue shown after creating a new save."""

    name = "welcome_dialogue"
    interval = 0.1
    stable_ticks = 2
    forget_after = 20

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._panes = None
        self._tracker = tracker

    def reset(self) -> None:
        self._panes = None

    def read(self, link) -> Optional[Hashable]:
        # This sequence runs before the tower exists. Requiring both the cold
        # grid value and a null entry keeps it apart from gameplay tutorials.
        if link.u8(ADDR_GRID_INDEX) != 0 or link.pointer(ADDR_GRID_ENTRY_PTR) is not None:
            return None
        if self._panes is None:
            self._panes = self._tracker.pane_index(link)

        # Finding the welcome message triggers a full sweep, whose successful
        # absence check distinguishes this layout from the resident file UI.
        self._panes.ensure(PANE_WELCOME_MESSAGES)
        if self._panes.live(PANE_FILE_PROMPT):
            return None

        active = []
        for name in PANE_WELCOME_MESSAGES:
            address = self._panes.address(name)
            if address is None:
                continue
            text = self._panes.text(name)
            if not text:
                continue
            flag = link.u8(address + MESSAGE_DISPLAY_FLAG_OFFSET)
            if flag is None:
                return None
            if flag & 1:
                active.append((name, text))
        if len(active) != 1:
            return None
        self._tracker.enter("welcome")
        return active[0]

    def describe(self, previous, current) -> Iterable[Utterance]:
        _pane, text = current
        return [Utterance(text, interrupt=True, priority=8)]


# In-game tutorial speech bubbles ("Ookii! (See what I do, then copy it!)").
# Some tutorials use several numbered panes rather than replacing one pane's
# text. Hole in One, for example, leaves its first line in T_message_00 and
# moves the follow-up to T_message_01. Reading only the first pane therefore
# returns the same snapshot forever and the engine correctly says nothing.
PANE_TUTORIALS = tuple(f"T_message_0{i}" for i in range(4))

class TutorialProbe(Probe):
    """Reads the tutorial bubbles shown when a game starts.

    Gameplay itself has no text panes at all, so this is the only text the
    game puts on screen once a game begins — and it is the part that explains
    what you are supposed to do.
    """

    name = "tutorial"
    interval = 0.1
    stable_ticks = 2
    forget_after = 40

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._panes = None
        self._tracker = tracker

    def reset(self) -> None:
        self._panes = None

    def read(self, link) -> Optional[Hashable]:
        # A live grid index means we are in the menu, not in a game.
        if link.u8(ADDR_GRID_INDEX) != INVALID_INDEX:
            return None
        if self._panes is None:
            self._panes = self._tracker.pane_index(link)
        # A layout may keep old lines in earlier numbered panes, so text being
        # present is not enough. Require exactly one non-empty pane whose
        # display flag is active; ambiguity must remain silent.
        self._panes.ensure(PANE_TUTORIALS)
        active = []
        for name in PANE_TUTORIALS:
            address = self._panes.address(name)
            if address is None:
                continue
            text = self._panes.text(name)
            if not text:
                continue
            flag = link.u8(address + MESSAGE_DISPLAY_FLAG_OFFSET)
            if flag is None:
                return None
            if flag & 1:
                active.append((name, text))
        if len(active) != 1:
            return None
        return active[0]

    def describe(self, previous, current) -> Iterable[Utterance]:
        _pane, text = current
        return [Utterance(text, interrupt=True, priority=7)]


# The epilogue shown after finishing a game: a caption and one or two lines of
# flavour text, e.g. "Scientific Findings" / "They sure were lively little
# creatures!" / "...And their color trails were so vibrant!"
#
# Note the capital C — these are different panes from the info card's
# lowercase "T_comment_00", which holds the button hints.
PANE_RESULT_CAPTION = "T_Caption_00"
PANE_RESULT_LINES = ("T_Comment_00", "T_Comment_01")

# Earning a Perfect puts its own message on screen, and on that screen the
# epilogue panes are empty — so the two arrive separately and either may be
# the only one with text:
#   '"Figure Fighter" You've earned a gift! Listen to it at the cafe!
#    There are now 47 gifts left to get. Keep going!'
PANE_PERFECT = "T_pft_00"

# Shown after a medal is awarded, e.g. Samurai Slice's "Thanks, mister! You're
# the best!". Capital M — this is NOT the tutorial's lowercase T_message_00.
# The game's pane names are case sensitive and it reuses words across screens,
# so check the case before assuming two panes are the same one.
PANE_REWARD = "T_Message_00"


class ResultProbe(Probe):
    """Reads what the game says after a game finishes.

    Covers the epilogue ("Scientific Findings ...") and the Perfect reward
    message, which are separate screens with separate panes.
    """

    name = "result"
    interval = 0.1
    stable_ticks = 2
    forget_after = 20

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._panes = None
        self._tracker = tracker

    def reset(self) -> None:
        self._panes = None

    def read(self, link) -> Optional[Hashable]:
        # Back on the tower means the epilogue is over.
        if link.u8(ADDR_GRID_INDEX) != INVALID_INDEX:
            return None
        # The mirror of the card's rule: an epilogue only follows a game, so
        # the grid must have been gone for a while. Without this, opening a
        # card would read out the previous game's epilogue.
        if self._tracker.since_grid() < ScreenTracker.RESULT_MIN_GAP:
            return None
        if self._panes is None:
            self._panes = self._tracker.pane_index(link)
        # Every one of these is optional: the epilogue and the Perfect message
        # are different screens, so ask for them all and use whatever is there.
        wanted = ((PANE_RESULT_CAPTION,) + PANE_RESULT_LINES
                  + (PANE_PERFECT, PANE_REWARD))
        self._panes.ensure(wanted)
        parts = tuple(self._panes.text(name) or "" for name in wanted)
        if not any(parts):
            return None
        return parts

    def describe(self, previous, current) -> Iterable[Utterance]:
        text = ". ".join(part for part in current if part)
        return [Utterance(text, interrupt=True, priority=8)]


# The "Notice!" dialog offering a Perfect attempt:
#   Notice! / "If you get a Perfect on Micro-Row right now, you'll receive its
#   music, also titled 'Micro-Row.'" / Press A!
# Unlike the info card's panes, these are cleared when the dialog is not up, so
# having text is itself a reliable signal that it is on screen.
PANE_NOTICE_TITLE = "T_title_spot_00"
PANE_NOTICE_BODY = "T_window_00"
PANE_NOTICE_PROMPT = "T_win_msg_sub_00"


class NoticeProbe(Probe):
    """Reads the Notice dialog, e.g. the offer of a Perfect attempt."""

    name = "notice"
    interval = 0.1
    stable_ticks = 2
    forget_after = 20

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._panes = None
        self._tracker = tracker

    def reset(self) -> None:
        self._panes = None

    def read(self, link) -> Optional[Hashable]:
        if link.u8(ADDR_GRID_INDEX) != INVALID_INDEX:
            return None
        if self._panes is None:
            self._panes = self._tracker.pane_index(link)
        wanted = (PANE_NOTICE_TITLE, PANE_NOTICE_BODY, PANE_NOTICE_PROMPT)
        self._panes.ensure(wanted)
        body = self._panes.text(PANE_NOTICE_BODY)
        if not body:
            return None
        title = self._panes.text(PANE_NOTICE_TITLE) or ""
        prompt = self._panes.text(PANE_NOTICE_PROMPT) or ""
        return (title, body, prompt)

    def describe(self, previous, current) -> Iterable[Utterance]:
        text = " ".join(part for part in current if part)
        return [Utterance(text, interrupt=True, priority=9)]


# The cafe's dialogue box. One pane, replaced line by line as the conversation
# is advanced, so speaking on change follows the whole exchange — the same shape
# as the tutorial bubbles.
#
# It keeps its last line after the conversation ends, sitting on the cafe menu
# reading "Come back soon!", so presence is not proof it is on screen. Its alpha
# is: 0 while it is down, ramping up as it opens.
PANE_CAFE_TALK = "T_talk_msg_00"

# The cafe menu's own options: Talk to Barista, Listen to Music, Read
# Something, Rhythm Test, Back. Read for their labels only — which one is
# highlighted lives somewhere not yet found, so they are not announced yet.
PANE_CAFE_MENU = tuple(f"T_menu_btn_0{i}" for i in range(5))


class CafeTalkProbe(Probe):
    """Reads what the barista says.

    Gated on the dialogue box being *drawn*, not merely resident: the pane
    keeps its last line for as long as the cafe is open, so anything weaker
    would announce "Come back soon!" on arriving at the cafe menu, before the
    barista had said anything.
    """

    name = "cafe_talk"
    interval = 0.1
    # The box fades in over ~0.3s and the text is already in place when it
    # starts, so confirm across a few polls rather than speaking mid-fade.
    stable_ticks = 3
    forget_after = 40

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._panes = None
        self._tracker = tracker
        self._conversation = 0
        self._open = False

    def reset(self) -> None:
        self._panes = None
        self._conversation = 0
        self._open = False

    def read(self, link) -> Optional[Hashable]:
        if self._panes is None:
            self._panes = self._tracker.pane_index(link)
        self._panes.ensure([PANE_CAFE_TALK])
        if not self._panes.visible(PANE_CAFE_TALK):
            self._open = False
            return None
        text = self._panes.text(PANE_CAFE_TALK)
        if not text:
            return None

        # Count conversations, and make the count part of the snapshot, so the
        # same line spoken in a later one is a change and gets announced. It
        # otherwise would not: when the barista has nothing new they repeat a
        # stock line, which is the line you hear most often, and relying on the
        # engine to have forgotten it means it is silent whenever you talk
        # again within a few seconds.
        #
        # Safe against a flicker re-announcing the current line only because
        # the box does not dip while a conversation is running — traced at
        # 0.1s across a whole exchange, the alpha held steady through every
        # line change and only fell at the end.
        if not self._open:
            self._open = True
            self._conversation += 1
        self._tracker.enter("cafe")
        return (self._conversation, text)

    def describe(self, previous, current) -> Iterable[Utterance]:
        _conversation, text = current
        return [Utterance(text, interrupt=True, priority=7)]


class SaveLabelProbe(Probe):
    """Speak the artwork labels offered while creating a new save."""

    name = "save_label"
    interval = 0.05
    stable_ticks = 3
    forget_after = 12

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._panes = None
        self._tracker = tracker
        self._intro = _IntroState()

    def reset(self) -> None:
        self._panes = None
        self._intro = _IntroState()

    def read(self, link) -> Optional[Hashable]:
        choice = self._tracker.save_label_selection(link)
        if choice is None:
            return None
        if self._panes is None:
            self._panes = self._tracker.pane_index(link)
        self._panes.ensure((PANE_SAVE_LABEL_PROMPT, PANE_SAVE_LABEL_BACK,
                            PANE_SAVE_LABEL_MII))
        prompt = self._panes.text(PANE_SAVE_LABEL_PROMPT)
        if not prompt:
            return None

        if isinstance(choice, int):
            if not (0 <= choice < len(SAVE_LABELS)):
                return None
            label = SAVE_LABELS[choice]
        else:
            label = self._panes.text(str(choice))
            if not label:
                return None

        # Choosing No on the confirmation dialog returns to the same grid, not
        # a new arrival, so do not replay the long heading in that direction.
        visit = self._tracker.enter("save_label", quiet_from=("save_confirm",))
        self._intro.update(visit)
        return (visit, choice, prompt, label)

    def describe(self, previous, current) -> Iterable[Utterance]:
        _visit, _choice, prompt, label = current
        intro = prompt if self._intro.take() else None
        return _with_intro(intro, label, priority=6)


class SaveConfirmProbe(Probe):
    """Speak the Continue? dialog and its highlighted No/Yes button."""

    name = "save_confirm"
    interval = 0.05
    stable_ticks = 3
    forget_after = 12

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._panes = None
        self._tracker = tracker
        self._intro = _IntroState()

    def reset(self) -> None:
        self._panes = None
        self._intro = _IntroState()

    def read(self, link) -> Optional[Hashable]:
        selected_pane = self._tracker.save_confirmation_selection(link)
        if selected_pane is None:
            return None
        if self._panes is None:
            self._panes = self._tracker.pane_index(link)
        wanted = (PANE_SAVE_CONFIRM_PROMPT, PANE_SAVE_CONFIRM_NO,
                  PANE_SAVE_CONFIRM_YES)
        self._panes.ensure(wanted)
        prompt = self._panes.text(PANE_SAVE_CONFIRM_PROMPT)
        label = self._panes.text(selected_pane)
        if not prompt or not label:
            return None
        visit = self._tracker.enter("save_confirm")
        self._intro.update(visit)
        return (visit, selected_pane, prompt, label)

    def describe(self, previous, current) -> Iterable[Utterance]:
        _visit, _selected_pane, prompt, label = current
        intro = prompt if self._intro.take() else None
        return _with_intro(intro, label, priority=7)


class FileSelectProbe(Probe):
    """Speaks the highlighted save slot on the file select screen.

    Slot contents come from the game: a slot with a save owns Flow and Medals
    panes, and a slot without them is an empty "New Game" box. The empty label
    is ours — the game draws those words as artwork, not text.

    Told apart from the game menu by which panes exist. Its own prompt pane
    has to be live, and the menu's card title must not be: the file select
    tears the menu's layout down, while the menu keeps the file panes resident
    (`T_no_data_00` still reads "Select one!" with the tower on screen), so
    presence alone proves nothing and absence is the half that does.

    This used to latch itself off for good the first time the grid index read
    anything but 0xFF, on the grounds that the file select only appears once
    per boot. It appears again whenever you go back to the title screen — and
    worse, the byte reads 0x00 on a cold boot, so the latch fired during the
    Wii logos and this screen never spoke at all.
    """

    name = "file_select"
    interval = 0.1
    # Loading a save tears this screen down over several seconds, during which
    # the slot byte sits in freed memory and takes on junk values. Demanding a
    # longer run of identical reads rides that out.
    stable_ticks = 8

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._panes = None
        self._tracker = tracker
        self._intro = _IntroState()
        self._slots = {}

    def reset(self) -> None:
        self._panes = None
        self._intro = _IntroState()
        self._slots = {}

    def read(self, link) -> Optional[Hashable]:
        slot = link.u8(ADDR_FILE_SLOT)
        # The label picker is another submenu in the same resident file layout,
        # so its stale file-slot panes otherwise look exactly like File 1.
        # Presence is deliberately broader than a settled selection. During a
        # cursor animation no grid coordinate is trustworthy, but this is still
        # the label screen; letting this probe claim that interval increments
        # the label screen's visit count and replays its heading on every move.
        if self._tracker.save_label_present(link):
            self._slots = {}
            return None
        if self._panes is None:
            self._panes = self._tracker.pane_index(link)
        # One unfiltered sweep caches every slot's panes at once, and asking
        # for the prompt is what triggers it. Asking for the Flow and Medals
        # panes instead would rescan forever on empty slots, whose panes
        # legitimately do not exist.
        self._panes.ensure([PANE_FILE_PROMPT])
        if self._panes.live(PANE_CARD_TITLE):
            # Definitely in the game menu. Drop what we learned about the
            # slots: the save is edited by playing, so the numbers we cached
            # last time are not the numbers this screen will show next time.
            self._slots = {}
            return None
        if not self._panes.live(PANE_FILE_PROMPT):
            return None
        if slot is None or slot >= FILE_SLOT_COUNT:
            return None

        flow = self._panes.text(PANE_FILE_FLOW.format(slot))
        medals = self._panes.text(PANE_FILE_MEDALS.format(slot))
        if flow and medals:
            text = f"File {slot + 1}. Flow {flow}. {medals} medals."
        else:
            text = f"File {slot + 1}. New game."

        # A slot cannot change contents while the screen is up, so the first
        # reading of each is the truth. If a later read disagrees, the panes
        # are being freed underneath us — stay quiet rather than downgrade a
        # real save to "New game".
        remembered = self._slots.setdefault(slot, text)
        if remembered != text:
            return None

        visit = self._tracker.enter("file")
        self._intro.update(visit)
        return (visit, slot, text)

    def describe(self, previous, current) -> Iterable[Utterance]:
        _visit, _slot, text = current
        intro = INTRO_FILE_SELECT if self._intro.take() else None
        return _with_intro(intro, text, priority=6)


def build_probes() -> List[Probe]:
    # One tracker shared by the screen probes so they agree on where we are.
    tracker = ScreenTracker()
    probes: List[Probe] = [GameIdentityProbe(), TitleScreenProbe(tracker),
                           WelcomeDialogueProbe(tracker),
                           GridCursorProbe(tracker), MenuButtonProbe(tracker),
                           InfoCardProbe(tracker),
                           FileSelectProbe(tracker), SaveLabelProbe(tracker),
                           SaveConfirmProbe(tracker),
                           TutorialProbe(tracker), ResultProbe(tracker),
                           NoticeProbe(tracker), CafeTalkProbe(tracker)]
    probes.extend(WatchProbe(w) for w in WATCHES)
    return probes
