# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

An external accessibility companion for **Rhythm Heaven Fever** (Wii, disc ID
`SOME01`) running in Dolphin. It attaches to Dolphin's emulated RAM from
outside, reconstructs what the player is interacting with from the game's own
data structures, and speaks it through NVDA via Tolk.

**Dolphin is never patched and the disc image is never modified.** If a change
would require either, it is out of scope.

The user of this project is a screen reader user. They cannot verify anything by
looking at the screen — which is the entire reason the companion exists, and
also why "it looks right" is never sufficient evidence here.

## Environment

Windows, Python 3.12, managed with uv. `uv sync` creates the environment and
installs the locked runtime dependencies plus the development tools (including
Pillow for `tools/sweep.py`).

```
uv run rhf-access                # the companion; --no-speech for console only
uv run python tools/diag.py       # can it see the game at all? run this first when wrong
uv run python tools/panes_dump.py # every live text pane — start here for a new screen
uv run python tools/trace.py 300  # watch state + panes change; start before booting
uv run python tools/step.py auto 12 S W  # automated memory scan (see below)
uv run python tools/sweep.py      # drive the menu, log + screenshot each stop
```

`trace.py` is the tool for "why is this screen silent": it logs the index, the
committed-selection byte, the entry pointer and which panes appear and vanish,
so a screen's signature can be read off directly instead of guessed. It is what
turned up the `0x00` boot value below.

Prefix anything that prints game text with `PYTHONIOENCODING=utf-8`. The game's
strings are UTF-16BE and contain characters (`é`, `♂`) that crash the default
cp1252 console encoding.

## Architecture

| File | Role |
| --- | --- |
| `rhfaccess/dolphin.py` | Hooking, reconnect, validated big-endian reads, pointer chasing |
| `rhfaccess/rawmem.py` | Direct MEM2 reader (see gotchas) |
| `rhfaccess/probes.py` | Confidence-gating engine: stability, dedupe, forgetting |
| `rhfaccess/speech.py` | Tolk/NVDA output, interrupt policy |
| `rhfaccess/games/archive.py` | Runtime locator for the game's DAT1 text archives |
| `rhfaccess/games/panes.py` | Reads on-screen text by NW4R layout pane name |
| `rhfaccess/games/rhf.py` | All RHF specifics: addresses, menu layout, probes |
| `tools/diag.py` | Health check: is MEM2 readable, is the archive there. Run first |
| `tools/scan.py` | Memory scanner core (numpy-vectorised, read-only) |
| `tools/delta.py` | Filters scan candidates by *how much* they moved |
| `tools/panes_dump.py` | Lists every live text pane — run this first for a new screen |
| `tools/trace.py` | Records screen state + pane changes over time; start it before booting |
| `tools/step.py` | One scan operation per invocation, state on disk |
| `tools/pad.py` | Synthetic input + window capture |
| `tools/sweep.py` | Menu walk producing a log plus screenshots |

Only `games/rhf.py` is game-specific. Everything else is reusable for another
Dolphin title.

## The core rule

**When in doubt, say nothing.** A wrong announcement is worse than silence,
because the user has no way to catch it. Concretely:

- Any read that cannot be verified returns `None`. Never substitute a default,
  and never zero-fill a failed read.
- A probe returns a snapshot or `None`; it never speaks directly. The engine
  requires the same snapshot on consecutive polls before trusting it.
- Corroborate with more than one piece of state. `GridCursorProbe` checks the
  index, the mirrored copy of that byte, and that the selected-entry pointer
  satisfies `pointer == base + index * 0x50` before it says anything.

## Verified game facts

Addresses in MEM1 (`0x80…`) have held across sessions. **Everything in MEM2
(`0x90…`) is heap and moves** — locate it at runtime, never hardcode it.

- `0x80320404` — game-select cursor index, u8. `0xFF` means no valid selection,
  **but only once the menu exists** — see the boot value below.
- `0x80320405` — **not a mirror**, despite looking like one. It holds the
  *committed* selection: it agrees with the index while the cursor is on the
  tower, and keeps the last entry when the index drops to `0xFF` for a card or
  a game. It reads `0xFF` itself only when nothing is selected at all.
- `0x80320430` — pointer to the selected item. Tower entries are a `0x50`-byte
  array; the buttons beside it are separate objects. Either way `+0x04` is a
  pointer to that item's NW4R pane, and the pane's ASCII name is at `+0xBC`.
- `0x8032A5C0` — **do not use.** It looked like a screen ID, and is not; see below.

**On a cold boot `0x80320404` reads `0x00`, not `0xFF`** — a perfectly valid
entry number, while the menu does not exist and `0x80320430` is still null.
Treating "not `0xFF`" as "the tower is up" is therefore wrong during boot, and
it cost the project three screens: `TitleScreenProbe` and `FileSelectProbe`
both latched themselves off permanently during the Wii logos, so the title
screen and file select never spoke at all. Corroborate with the entry pointer.

Screens are told apart by `ADDR_GRID_INDEX`: a valid entry number while moving
around the tower, `0xFF` once the cursor is handed to a card, a button, a game,
or the file select. Everything behind `0xFF` needs a second signal:

| screen | index | `+1` | entry pointer | text panes |
| --- | --- | --- | --- | --- |
| tower | 0–54 | = index | base + index·`0x50` | 45 |
| button row | `0xFF` | `0xFF` | that button's object | 45 |
| info card | `0xFF` | last index | the game's entry | 45 |
| gameplay | `0xFF` | last index | the game's entry | 0, then a few |
| title screen | `0xFF` or `0x00` | `0xFF` or `0x00` | sentinel, or null on boot | **0** |
| file select | `0xFF` | `0xFF` | sentinel | 28 |

The new-save label picker reuses that resident file layout, so the file panes
alone misidentify it as File 1. Its fifteen labels are artwork, but its live
`N_cursor_frm_00` pane moves to the transform of `N_name_00`–`14`. Locate that
cursor pane by name at runtime; the same name also occurs in serialized layout
data, so require usable alpha and a coordinate matching a selectable target.
After gameplay, two other live layouts can retain `N_cursor_frm_00` objects at
`(-210.62, -185)` and `(207.62, -286)`. Neither is a save-label target. The
locator once checked only broad finite-coordinate bounds despite its comment;
after a companion restart all three matched and the label screen went silent.
Initial discovery must call `_choice_at()` and accept only the cursor settled on
one of the fifteen labels or the Back/Choose Mii buttons.
The cursor and child text remain live after returning to file select, so they
do not prove the label screen is active. Its parent `W_menu_01` does: it sits at
Y=-34 on the label screen and is parked at Y=-500 on file select. Require that
parent at its on-screen transform before suppressing `FileSelectProbe`.
The labels are authored from the US screen, like `EXTRAS`; Back and Choose Mii
come from their text panes.

The confirmation dialog after choosing a label uses that same cursor at
`(-95, -126)` for No and `(95, -126)` for Yes. Its prompt and labels are text
panes (`T_msg_02`, `T_NG_btn_00`, `T_OK_btn_00`). Returning from No is a quiet
return to the label grid and must not replay the grid's heading.

An existing file opens a separate action group, `W_menu_00`, at `(0, -112)`.
Its `N_cursor_frm_00` shares that group's `RootPane` and lands on Start
`(0, -29)`, Back `(-117.65, -147)`, Delete `(0, -147)`, or Change
`(117.65, -147)`. Start is artwork; the other labels have `T_back_btn_00`,
`T_delete_btn_00`, and `T_change_btn_00`. `FileActionProbe` announces the file
summary once and then the selected action. The ordinary selected-entry pointer
can retain an unrelated game-menu Back object here, so `MenuButtonProbe` must
yield whenever the file-action group is on screen. Conversely, that group and
its cursor remain resident after reaching the game menu; `ADDR_FILE_SLOT` is a
valid 0-based slot on the file-action screen and `0xFF` on the game menu, so the
cursor detector requires both signals before claiming the screen.

Selecting Delete overlays that action group without hiding or moving its cursor,
so the action probe still sees Delete underneath. The warning is `T_msg_00` and
its only button is `T_back_btn_01`. Their alpha remains 255 even after the dialog
closes, so alpha alone is not visibility: their ancestor `W_msg_00` is at
`(0, -112)` while displayed and parked at Y=-420 afterward. Both probes verify
that ancestor transform. `DeleteConfirmProbe` owns the displayed dialog, and
`FileActionProbe` yields only while it is actually on screen. `panes.clean()`
converts the game's literal circled digits `①` and `②` to speakable `1` and `2`.
The ordinary grid byte reads `0`, not `0xFF`, while this overlay is open, so it
must not be used to reject the delete confirmation.

The card and the post-game screens both sit behind `0xFF` with the pointer on
the same entry, so they are separated by *timing* — a card opens straight off
the grid, an epilogue can only follow a game. That is a heuristic and is
labelled as one in `ScreenTracker`. Everything else above is structural.

Result ranks are artwork, not text. The result layout exposes the mutually
exclusive containers `N_HI_00` (Superb), `N_OK_00` (OK), and `N_NG_00` (Try
Again), plus `N_Medal_00`. On a verified Superb-with-medal screen, the low flag
bit at name `-1` was set for `N_HI_00` and `N_Medal_00`, and clear for the other
two ranks. `ResultRankProbe` locates all four names at runtime, follows their
NW4R parent chains, and accepts only objects sharing the visible
`T_Caption_00` pane's `RootPane`; never hardcode the observed heap addresses.
It requires exactly one active rank and queues the rank after `ResultProbe`'s
feedback rather than interrupting it.

The feedback layout is freed before the following Perfect/post-medal message
layout is created. A shared `PaneIndex` sweep can land in that empty handoff and
then wait its normal five-second rescan interval, delaying or missing the short
follow-up screen. `ResultProbe` detects its caption disappearing and briefly
refreshes the shared index at 250 ms until `T_pft_00` or `T_Message_00` appears.
Keep that refresh confined to the transition; continuous MEM2 sweeps noticeably
slow the poll loop.

## Reading on-screen text (start here for any new screen)

The UI is NW4R layouts. Every text box is an object holding its own ASCII pane
name (`T_game_title_00`, `T_exposition_00`) with a pointer to its live UTF-16BE
string at name + `0x1C`. Ask for text **by pane name** — do not go hunting for
individual buffers, which is slow and yields addresses that move.

`panes.PaneIndex.scan()` with no arguments returns every live text pane. That is
the first thing to run when adding support for a new screen: it shows what the
screen exposes and what the names are.

**A pane keeps its last string after its screen closes.** Never treat the
presence of text as proof that it is on screen.

**But the pane does say whether it is being drawn.** Its effective alpha sits
three bytes ahead of its name (`panes.ALPHA_OFFSET`), reads `0` while the pane
is not drawn, and ramps up as it fades in. `PaneIndex.visible()` wraps it.
Verified on the café's dialogue box — `0x00` → `0x83` → `0xDC` opening, back to
`0x00` closing, while the menu buttons beside it held `0xFF` throughout and a
button that was not on screen held `0x00`.

It rests at `0xDC` rather than `0xFF` on that box, so this is a **threshold**
(`VISIBLE_ALPHA`), not an equality test, and it is briefly false at the start of
a fade — which the engine's stability requirement absorbs.

This is the general answer to "the pane is live but off screen", and it is
better than any of the state gates around it. `CafeTalkProbe` relies on it
entirely: the café keeps "Come back soon!" in its dialogue pane for as long as
the café is open, and nothing else on that screen distinguishes the two.
The info card's grid-index gate and the card-versus-epilogue timing heuristic
both predate this and could likely be replaced by it.

**Absence is usable evidence, but only via a full sweep *that succeeded*.**
`scan()` returns `None` — not `{}` — when it could not read MEM2, and callers
must tell those apart. "No panes" is what identifies the title screen, so a
sweep that merely failed must never be allowed to look like one; when it was,
the companion announced the title screen over the button row, the info card and
the café, and went silent everywhere text is read. `None` means unverified and
the sweep neither forgets nor reports a count.

`PaneIndex.live()`
answers "was this pane there last sweep", and `scan()` drops what it no longer
finds so that answer means something — freeing a layout leaves the ASCII name
in the heap, and `address()` re-checks nothing else, so without the pruning a
pane would read as present forever and `text()` would decode freed memory. The
file select used to be identified this way: its own prompt live, and the game
menu's `T_game_title_00` *not* live. That failed after playing and returning
through the title screen: the freed card name could remain cached while no
missing pane forced a new sweep, silencing file select until the companion
restarted. File select requires both a valid `ADDR_FILE_SLOT` and visible
`T_no_data_00`; the prompt can retain alpha 255 behind the game menu, where the
slot byte is `0xFF`. `MenuButtonProbe` uses the same combined check so the file
layout's Back pane cannot interrupt the slot reading.

**Menu items are pane-backed, which is how the buttons are read.** The selected
item's object at `ADDR_GRID_ENTRY_PTR` holds a pane pointer at `+0x04`, and
that pane's name sits at `+0xBC`. Container panes are `N_…` and their text
panes `T_…`, so `N_2play_btn_00` → `T_2play_btn_00` → "Two Player". This is the
only way to name the button row: the cursor index reads `0xFF` for all of it,
so there is no index to look a button up by. Tower entries are pane-backed too
(`N_game_btn_13`, extras included) but have no matching `T_` pane — their names
are artwork, which is why `EXTRAS` is hardcoded — and `TOWER_PANE` skips them
rather than sweeping MEM2 for something that cannot exist.
Names are reused across resident layouts, so `MenuButtonProbe` accepts only the
`T_` pane whose parent pointer is the selected `N_` container. On the game-menu
Back button, three `T_back_btn_00` panes simultaneously held Back, Title Screen,
and One Player; parent identity selected the visible Title Screen label.

**A pane can also be live while off screen.** The info card's title and
description track the highlighted game as you scroll the grid, with no card
displayed. Pane text is what the game *would* draw, never proof that it is on
screen. Gate on a property of the screen — for the card, `ADDR_GRID_INDEX ==
0xFF` — not on the text changing.

Not every pane behaves that way: the Notice dialog's panes are *cleared* when it
is down, so there, having text really is proof. Check which kind you have before
deciding on a gate — or just use `visible()`, which does not care.

There are several Notice layouts. Perfect-attempt notices use
`T_title_spot_00` / `T_window_00` / `T_win_msg_sub_00`; unlock notices use the
parallel `T_win_title_01` / `T_win_msg_01` / `T_win_msg_sub_01` and `_02` sets.
On the Rhythm Toys unlock, `_01` held the visible text while `_02` was resident,
transparent and empty. `NoticeProbe` checks every set and requires exactly one
visible, nonempty body before speaking.

The large modal used by the two-player menu is separate again:
`T_RemoteMsg_00` holds its body and `T_RCloseMsg_00` its prompt. Both retain
other controller-related strings when not shown, so `RemoteMessageProbe`
requires both their visible alpha and visible alpha on ancestor
`W_RemoteFrm_00`. The children remain at 255 on the ordinary game menu while
that ancestor is 0. A modal can cover a still-selected menu button;
`MenuButtonProbe` applies the same effective-visibility check and stays silent
so the underlying button cannot interrupt the dialog, particularly after a
companion restart.

The gameplay pause overlay exposes `T_pause_msg_00`, whose literal text is only
`?`, plus localized actions `T_msg_bln_00` = Continue and `T_msg_bln_01` = Quit.
Those children retain readable text and alpha outside the real pause menu, so
they are not visibility markers by themselves. All three descend from
`N_cntrl_00`; `PauseProbe` requires that ancestor's alpha and low display bit.
On the open menu it measured alpha 255 and flags 7. The screen's arrows map Plus
to Continue and Minus to Quit; that mapping is visual and therefore authored.
Old tutorial panes remain resident on this screen but their low display bits
are clear.

**Pane names are case sensitive and the game reuses words.** `T_message_00` is
the tutorial bubble; `T_Message_00` is the post-medal message. Different
screens, one letter apart.

**Tutorials can rotate through numbered panes.** Hole in One retains its first
line in `T_message_00` and puts the next one in `T_message_01`. For these panes,
the low bit at name `-1` distinguished the retained line (`0`) from the current
one (`1`). `TutorialProbe` checks `T_message_00`–`03` and requires exactly one
non-empty pane with that bit set before speaking. This flag is only verified for
numbered message layouts; do not treat it as a general NW4R visibility signal.
Pane names are not necessarily unique: Screwbot Factory kept one hidden, empty
`T_message_00` object before a second `T_message_00` containing *"Your job will
be to screw the robots' heads on."* `PaneIndex` therefore retains every runtime
match, and `TutorialProbe` evaluates the text and flag of each duplicate rather
than trusting the first address found.
The new-save welcome sequence uses `T_message_00`/`01` too, but it runs with the
cold grid value and null entry pointer rather than gameplay's `0xFF`. Its probe
also requires the resident file prompt to be absent after a successful sweep.

**Share the pane index.** Every probe must take its `PaneIndex` from
`ScreenTracker.pane_index()`. A sweep costs ~0.14s and a probe sweeps whenever a
pane it wants is absent, which is most of the time. Four probes with their own
index pushed poll ticks past 800ms and speech lagged seconds behind the screen.

`0x8032A5C0` is **not** a screen ID, despite looking like one when found by
driving select/back through the scanner. A live trace showed it reading 3 while
scrolling the grid. Gating on it silenced the grid and made the card announce
on every cursor move.

Menu layout (see README for the full table): the cursor walks one array. idx 0–2
are the extras, 3–4 locked, then each block of five is a row of four games plus
that row's remix. **A/D jump a whole row (±5); W/S move within a column.**

The text archive stores games in one consecutive run and remixes in a separate,
earlier one, so no single offset can map cursor index to name — that bug
announced "Fork Lifter" for Remix 1 and drifted further every row. Use
`rhf.label_for()`.

Verified against the on-screen banner: extras, rows 1–3. Rows 4–10 follow from
the archive structure but are locked in the save and unconfirmed.

## Gotchas

- **dolphin-memory-engine cannot read MEM2** on current Dolphin builds; every
  read into `0x90000000+` throws. `rawmem.py` locates the emulated memory in the
  host process and `DolphinLink` falls back to it transparently. Do not
  "fix" this by dropping MEM2 support — most of the game's state is there.
- **Losing MEM2 does not look like losing MEM2.** The raw backend goes stale
  whenever Dolphin remaps its arena — restarting the game is enough — and the
  handle stays open, so nothing reports a disconnect. MEM1 keeps working through
  dme, so the companion still hooks, still names the game and still walks the
  tower over the hardcoded `EXTRAS`, while every game name (archive, MEM2) and
  every screen that reads text (panes, MEM2) goes quiet. **That combination —
  "Rhythm Café" and "Rhythm Toys" speak but no game does, plus the title screen
  announced over other screens — is the signature, and it is a memory fault, not
  a menu bug.** `tools/diag.py` tells them apart in one command. The link now
  re-attaches on its own and `app.py` says so out loud, but the failure is worth
  recognising because it will come back in a new shape.
- **Anything caching an address must survive a re-attach.** `DolphinLink.generation`
  is bumped on every attach; `PaneIndex` clears itself when it changes. An
  address learned before a remap describes memory that is no longer the game,
  and re-reading the ASCII name at it is not enough of a check to catch that.
- **Run only one `run.py`.** Instances speak independently, and one started
  before a code change keeps announcing the old behaviour. Duplicate speech is
  almost always a stray process, not a logic bug.
- **Dolphin's keyboard device is DirectInput**, so synthetic input must use
  scancodes (`KEYEVENTF_SCANCODE`). Virtual key codes are ignored. Mapping is in
  `%APPDATA%\Dolphin Emulator\Config\WiimoteNew.ini` (currently W/A/S/D = D-Pad,
  Z = A).
- **Screenshots:** the game prints the highlighted entry's name on screen, which
  is the only ground truth available. The banner fades in ~1.3s after the
  selection settles, and is anchored *under the selected card*, so a fixed crop
  misses it. The user's Dolphin window is small (174×198); enlarge it to read
  text and **restore it afterwards**.

## Finding new addresses

Use `tools/step.py auto <cycles> <key> <back-key>`. Each cycle presses a key and
requires the value to change, then presses the reverse and requires it to be
identical to its origin value again. This is direction-agnostic and kills
oscillators (a value flipping every frame survives a cycle only 25% of the
time). Typically 25 million candidates to a handful in three cycles.

Do not hand-scan with single-sample `changed`/`unchanged` comparisons. RAM is
full of double buffers and animation counters that pass those by coin flip; that
approach repeatedly converged on garbage before automation replaced it.

## Working with the user

They cannot see the screen. When something needs reading off it, that is your
job: enlarge the Dolphin window, screenshot, read it, and **restore the window**
(theirs is 174x198 at 681,298 — they do not need it visible).

- Check game state before sending input. Pressing keys while they are mid-game
  interferes with play.
- `pad.tap` releases in a `finally` and `pad.grab` calls `release_all` first. A
  lost keyup leaves Dolphin holding the D-pad and the cursor scrolls by itself —
  which reads exactly like a memory-reading bug and is not one.
- Run test copies of `run.py` under `timeout` and confirm none survive. Two
  stray instances once talked over each other, and the older one was announcing
  pre-fix names.
- The engine swallows probe exceptions and treats them as "unverified", so a
  broken probe goes *quiet* rather than crashing. After refactoring, call
  `read()` on every probe and check for errors — suspiciously fast ticks mean
  probes are failing, not that the code got faster.

There is a copy of the project memories in `docs/memory/`.

## Style

Match the existing code: type hints, `Optional` returns for anything that can
fail, comments that explain *why* a check exists rather than restating it. When
a fact is provisional, say so where the code is — and keep the README's
"Provisional" section honest, because it is the handover between sessions.
