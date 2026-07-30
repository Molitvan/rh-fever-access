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

INVALID_INDEX = 0xFF
MAX_NAME_LENGTH = 32

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

    def note_grid(self, active: bool) -> None:
        if active:
            self.last_grid_seen = time.monotonic()

    def since_grid(self) -> float:
        if not self.last_grid_seen:
            return float("inf")
        return time.monotonic() - self.last_grid_seen

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

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._archive = None
        self._entry_base = None
        self._tracker = tracker
        self._intro = _IntroState()
        self._last_set = None

    def reset(self) -> None:
        self._archive = None
        self._entry_base = None
        self._intro = _IntroState()
        self._last_set = None

    def _text_archive(self, link):
        if self._archive is not None and self._archive.still_valid():
            return self._archive
        self._archive = archive.find(link)
        return self._archive

    def read(self, link) -> Optional[Hashable]:
        index = link.u8(ADDR_GRID_INDEX)
        self._tracker.note_grid(index is not None and index != INVALID_INDEX)
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

        # Coming back from a game's info card is not a fresh arrival.
        visit = self._tracker.enter("grid", quiet_from=("card",))
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
        # A card opens straight off the grid. If the grid has not been active
        # for seconds we are somewhere else behind a 0xFF index — in a game, or
        # on the epilogue — and the card's panes are merely stale.
        if self._tracker.since_grid() > ScreenTracker.CARD_MAX_GAP:
            return None
        if self._panes is None:
            # Before any card has been opened these panes do not exist, and a
            # sweep costs about a second — so look rarely rather than every
            # few seconds while sitting on some other 0xFF screen.
            self._panes = panes.PaneIndex(link, rescan_interval=8.0)
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


# In-game tutorial speech bubbles ("Ookii! (See what I do, then copy it!)").
# Advancing the tutorial replaces the text in this one pane, so speaking on
# change follows the whole sequence.
PANE_TUTORIAL = "T_message_00"


class TutorialProbe(Probe):
    """Reads the tutorial bubbles shown when a game starts.

    Gameplay itself has no text panes at all, so this is the only text the
    game puts on screen once a game begins — and it is the part that explains
    what you are supposed to do.
    """

    name = "tutorial"
    interval = 0.15
    stable_ticks = 3
    forget_after = 40

    def __init__(self) -> None:
        self._panes = None

    def reset(self) -> None:
        self._panes = None

    def read(self, link) -> Optional[Hashable]:
        # A live grid index means we are in the menu, not in a game.
        if link.u8(ADDR_GRID_INDEX) != INVALID_INDEX:
            return None
        if self._panes is None:
            # This pane does not exist until a game has started, and a sweep is
            # expensive, so look for it rarely rather than every few seconds.
            self._panes = panes.PaneIndex(link, rescan_interval=10.0)
        if not self._panes.ensure([PANE_TUTORIAL]):
            return None
        text = self._panes.text(PANE_TUTORIAL)
        if not text:
            return None
        return text

    def describe(self, previous, current) -> Iterable[Utterance]:
        return [Utterance(current, interrupt=True, priority=7)]


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


class ResultProbe(Probe):
    """Reads what the game says after a game finishes.

    Covers the epilogue ("Scientific Findings ...") and the Perfect reward
    message, which are separate screens with separate panes.
    """

    name = "result"
    interval = 0.15
    stable_ticks = 3
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
            self._panes = panes.PaneIndex(link, rescan_interval=8.0)
        # Every one of these is optional: the epilogue and the Perfect message
        # are different screens, so ask for them all and use whatever is there.
        wanted = (PANE_RESULT_CAPTION,) + PANE_RESULT_LINES + (PANE_PERFECT,)
        self._panes.ensure(wanted)
        parts = tuple(self._panes.text(name) or "" for name in wanted)
        if not any(parts):
            return None
        return parts

    def describe(self, previous, current) -> Iterable[Utterance]:
        text = ". ".join(part for part in current if part)
        return [Utterance(text, interrupt=True, priority=8)]


class FileSelectProbe(Probe):
    """Speaks the highlighted save slot on the file select screen.

    Slot contents come from the game: a slot with a save owns Flow and Medals
    panes, and a slot without them is an empty "New Game" box. The empty label
    is ours — the game draws those words as artwork, not text.

    Gated on the game grid being inactive, so it stays quiet once play starts.
    """

    name = "file_select"
    interval = 0.1
    # Loading a save tears this screen down over several seconds, during which
    # the slot byte sits in freed memory and takes on junk values. Demanding a
    # longer run of identical reads rides that out.
    stable_ticks = 8

    def __init__(self, tracker: "ScreenTracker") -> None:
        self._panes = None
        self._scanned = False
        self._tracker = tracker
        self._intro = _IntroState()
        self._slots = {}
        self._finished = False

    def reset(self) -> None:
        self._panes = None
        self._scanned = False
        self._intro = _IntroState()
        self._slots = {}
        self._finished = False

    def read(self, link) -> Optional[Hashable]:
        if self._finished:
            return None
        # A valid grid index means we are in the game tower. The file select
        # only ever appears once, before that, so once the tower is up this
        # screen is gone for good and must never speak again.
        if link.u8(ADDR_GRID_INDEX) != INVALID_INDEX:
            self._finished = True
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
    probes: List[Probe] = [GameIdentityProbe(), TitleScreenProbe(),
                           GridCursorProbe(tracker), InfoCardProbe(tracker),
                           FileSelectProbe(tracker), TutorialProbe(), ResultProbe(tracker)]
    probes.extend(WatchProbe(w) for w in WATCHES)
    return probes
