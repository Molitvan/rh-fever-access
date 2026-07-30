# rhf-access

An external accessibility companion for **Rhythm Heaven Fever** (Wii) running in Dolphin.

Nothing in Dolphin is patched and nothing in the disc image is modified. The companion
attaches to Dolphin's emulated RAM from outside, reconstructs what the player is
currently interacting with from the game's own data structures, and speaks it through
NVDA via Tolk. Same architecture as the Pokémon Battle Revolution companion — the
emulator is just a window onto live game state.

## Status

Working today:

- **Game-select cursor** — move between entries and it announces "Screwbot Factory",
  "See-Saw", "Double Date" and so on, read from the game's own text archive rather than
  from a hardcoded list.
- **Game info card** — select a game and it reads the card: *"Tambourine. Ready to play
  a little Simian Says on the tambourine?"*
- **Save file select** — *"File 1. Flow 89. 16 medals."* / *"File 2. New game."*
- **Tutorial bubbles** — *"Ookii! (See what I do, then copy it!)"*, following the
  sequence as you advance it. Gameplay itself has no text panes at all, so this is the
  only text the game shows once a game begins.
- **Set changes** — left/right jump a whole set, so those announce *"Set 2. Fork
  Lifter"*; moving within a set just names the game.
- **Post-game epilogue** — *"Scientific Findings. They sure were lively little
  creatures! ...And their color trails were so vibrant!"*
- **Perfect rewards** — *"'Figure Fighter' You've earned a gift! Listen to it at the
  café! There are now 47 gifts left to get. Keep going!"* (pane `T_pft_00`; on that
  screen the epilogue panes are empty, so the two arrive separately)
- **Title screen** — an authored prompt (see below), because the screen has no text.

### Telling the info card apart from the epilogue

Both sit behind a `0xFF` grid index and both keep live panes, so neither can be
identified from its text. They are separated by timing instead: a card is opened
straight off the grid (`< 3s` since the grid was active), while an epilogue can only
follow a game, which takes longer than that (`> 5s`). It is a heuristic, and it is
written down as one in `ScreenTracker`.

The **rank** (Try Again / OK / Superb) is *not* covered — it appears to be artwork
rather than text, and no rank pane showed up in a sweep of the epilogue screen.

### Reading on-screen text in general

The game draws its UI with Nintendo's NW4R layout system, and this turned out to be the
key to everything. Each text box is a runtime object carrying its own ASCII pane name —
`T_game_title_00`, `T_exposition_00` — with a pointer to its live UTF-16BE string at
name + `0x1C`. So text can be fetched *by name* instead of by hunting for one buffer at
a time, and it should extend to dialogue, results and other screens.

`rhfaccess/games/panes.py` implements this: scan MEM2 for the name once, cache it,
re-validate cheaply per read. `PaneIndex.scan()` with no arguments dumps every live
text pane, which is the fastest way to find what a new screen exposes.

Two catches, both learned the hard way:

**A pane keeps its last string after its screen goes away**, and nothing in the pane
says whether it is visible. Every use must be gated on game state.

**Worse, a pane can be live but not on screen.** The info card's title and description
panes are kept in step with the highlighted game *while you scroll the grid*, with no
card displayed — the game is preloading them. Reading them on change therefore
announced a description for every cursor move. Pane text is what the game *would*
draw, not proof that it is drawing it.

The card is gated on the grid index instead: it holds a valid entry number while you
move around the tower and `0xFF` once the cursor is handed to the card. That is a
property of the screen rather than of the text, which is what makes it trustworthy.

### Verified

| What | Where | How it was confirmed |
| --- | --- | --- |
| Game-select cursor index | `0x80320404` (u8, mirrored at `+1`) | Automated return-to-origin scan; walks ±1 per press, `0xFF` when invalid |
| Selected entry object | `0x80320430` → MEM2, 0x50-byte stride | Pointer moves exactly one stride per press |
| Text archive (`DAT1`) | located at runtime | 292 records; index 1 = "Title Screen", 104 = "Hole in One" |
| ~~Menu state~~ `0x8032A5C0` | **do not use** | Looked like 1 = grid / 3 = card when found by driving Z/X. A live trace while scrolling showed it reading 3 throughout. Not a screen ID |
| File slot index | `0x90DEBB71` (u8) | 0-3 over the 2x2 slot grid; survived a full game reboot at the same address |
| Layout text panes | located by name at runtime | pointer at name + `0x1C`; verified against the on-screen card |
| MEM2 access | `rhfaccess/rawmem.py` | dolphin-memory-engine cannot read MEM2 on current Dolphin builds |

### Menu layout

The cursor walks a single array. Left column is the extras, then each further
column is one row of the game tower, bottom to top:

| cursor index | entry | name comes from |
| --- | --- | --- |
| 0–2 | Rhythm Café, Rhythm Toys, Endless Games | fixed labels (see below) |
| 3–4 | locked "?" entries | nothing — stays silent |
| 5–9 | row 1: four games, then Remix 1 | archive 104–107, then 55 |
| 10–14 | row 2, and so on for 10 rows | archive 108–111, then 56 |
| 255 | not on the grid at all | nothing — stays silent |

W/S move within a column; **A/D jump a whole row** (±5), they do not move
between columns of a single row.

The archive stores games as one consecutive run (104–143, 40 of them) and
remixes as a separate earlier run (55–64). That is why a single linear offset
can never work: it holds for the four games of a row and slips by one at every
remix. Announcing "Fork Lifter" for Remix 1, then being one further out on
every later row, is the exact signature of that bug.

For the extras the archive holds internal scene names ("Rhythm Toy Menu",
"Café") rather than the on-screen labels ("Rhythm Toys", "Rhythm Café"), so
those three are fixed strings read off the banner. The games are different —
there the archive text and the on-screen banner match exactly, so nothing is
hardcoded for them.

Corroboration before speaking: the selected-entry pointer must satisfy
`pointer == base + index * 0x50` with a base that holds still. When the cursor
leaves the grid the pointer jumps to a different array and that breaks, which
is when the companion goes quiet instead of repeating a stale name.

### The title screen has no text

A pane sweep on the title screen returns **zero** text panes — the "press A and B"
prompt is a picture of a Wii Remote, not a string. So `TITLE_ANNOUNCEMENT` in `rhf.py`
is authored rather than read from the game, the same compromise as the extras labels.
It is justified here because staying silent leaves a blind player with no way to know
what to press.

Its detector is the weakest thing in the project: "a full pane sweep found nothing".
That is distinctive today (every other screen exposes 20+ panes) but the boot logos
presumably also have none, so it may announce early. The probe switches itself off for
good as soon as any other screen appears, so the sweep only runs for a few seconds.

### Heap addresses are reproducible

Worth knowing before doing more RE: RHF's allocator is deterministic. The text archive,
the description buffer, the file-select panes and the slot index all reappeared at
*identical* addresses across a full game reboot. MEM2 addresses can therefore be used
directly, with validation — though locating by pane name or archive magic is still
preferred where possible.

### Provisional — do not trust yet

- **Rows 4–10 are unverified.** Confirmed against the on-screen banner: the extras
  (idx 1 = "Rhythm Toys"), all of row 1, row 2 (idx 11 = "Tambourine") and row 3
  (idx 19 = "Remix 3", with the Air Rally badminton icon sitting at idx 17 as
  predicted). Rows 4–10 follow from the archive's structure but are still locked in
  the save file, so they have not been seen on screen.
- **Locked entries say nothing.** Indices 3–4 are the "?" placeholders. Whether the
  game has a name for a locked row's entries is unknown.
- **The extras labels are English, from the US build.** The games are language-neutral
  (read from the archive), but `EXTRAS` is three hardcoded strings and would need
  redoing for another region.
- **Address stability across a reboot is untested.** `0x80320404` is in MEM1 and likely
  fixed, but nothing here has survived a cold boot yet. Everything in MEM2 definitely
  moves, which is why the archive is located at runtime rather than hardcoded.
Nothing else is claimed. `WATCHES` is still empty; the companion stays silent about
anything that has not been verified.

### Debugging by screenshot

`tools/sweep.py` drives the cursor and saves, at every stop, the index, the name the
companion *would* speak, and a PNG of the frame. The game prints the highlighted
entry's name across the bottom of the screen, so each capture carries its own ground
truth — which is how the row-2 and extras mappings were pinned down rather than
guessed. Two things to know: the banner fades in only after the selection settles
(wait ~1.3s before capturing), and it is anchored under the selected card rather than
at a fixed screen position, so a fixed crop will miss it.

## Install

```
pip install -r requirements.txt
```

## Run

Start Dolphin, boot Rhythm Heaven Fever, then:

```
python run.py
```

Options: `--hz 30` (poll rate), `--no-speech` (console only), `--quiet` (no console echo).

**Run only one copy.** Each instance speaks independently, so two of them talk over
each other — and because Python loads the code at startup, an instance left running
from before a change keeps announcing the old names. If you hear every entry twice,
that is what it is:

```
powershell -c "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" |
  Where-Object { $_.CommandLine -like '*run.py*' } | Select ProcessId, CommandLine"
```

## Why this game is a different problem from a Pokémon battle

In a turn-based RPG, almost everything a blind player needs is text and discrete state:
HP totals, move names, menu cursors. Rhythm Heaven Fever is the opposite — the core loop
is *already* audio. The beat, the cues, the "ready?" call are all sound by design, which
is why the series has always been unusually playable without sight.

So the accessibility gap is not the rhythm. It is everything around it:

- **Menu and flow state** — title screen, the game grid, which cell the cursor is on,
  whether a game is locked, medals, Café, Rhythm Toys, Endless Games.
- **Instruction and practice screens** — these are text and visual demonstration.
- **Results** — the rank you were just given (Try Again / OK / Superb), unlock
  notifications, whether Perfect is available.
- **The specific games with visually-gated cues**, where a beat is unambiguous by ear
  but the *target* is not.

That ordering is deliberate: menus first, because that's what makes the game reachable
at all, and it's also the easiest state to verify.

## How a value gets from memory to speech

1. **Find it** with `tools/scan.py` (see below) — read-only, never writes to the game.
2. **Corroborate it.** A single address is not enough; values get reused and structures
   get rebuilt between scenes. A `Watch` carries a `valid()` range check and an optional
   `gate()` context check, and the probe engine additionally requires the same value to
   read identically for `stable_ticks` consecutive polls before it is trusted.
3. **Speak only changes.** Identical consecutive text is dropped; cursor movement
   interrupts so fast scrolling announces the destination, not a backlog.
4. **When in doubt, stay silent.** Any failed read, out-of-range value, or broken pointer
   chain returns `None`, which the engine treats as "unverified" — never as news.

## Finding an address

The fast way is to let the tool press the buttons. `tools/pad.py` sends scancodes to
Dolphin (its keyboard device is DirectInput, so virtual key codes are ignored), and
`step.py auto` uses that to run a **return-to-origin** scan:

```
python tools/step.py auto 12 S W 0.45 --mem2
```

Each cycle presses Down and requires the value to *change*, then presses Up and requires
it to be *identical to its origin value* again. That invariant is direction-agnostic —
it never assumes Down means "increment", which matters for a grid, or for a menu storing
an entry ID rather than a position. An oscillator passes each half by coin flip, so it
survives a full cycle 25% of the time and is gone within a few cycles. In practice this
goes from 25 million candidates to four in three cycles.

That last point is the whole reason for automation. Comparing two instants by hand
(`changed` / `unchanged`) lets any value that flips every frame through half the time,
and RAM is full of double buffers, audio state, and animation counters. Hand-scanning
kept converging on those. Fifty machine-driven cycles do not.

Manual stepping still exists when you need it — `step.py` takes one operation per run so
you can move the cursor between steps:

```
python tools/step.py new u32
python tools/step.py changed        # after moving
python tools/step.py hold 8 2       # holds still across 8 samples, not 2
python tools/step.py list
python tools/step.py drive 4 S W    # step the cursor, print every candidate
python tools/step.py track 20 30    # rank candidates by how index-like they behave
```

Then reboot the game and re-check the address. Wii titles load at fixed addresses far
more often than PC games do, but anything living in a heap allocation will move — if it
does, find a stable pointer to it and use `Watch(offsets=...)`, which validates every hop.

Add the confirmed result to `WATCHES`:

```python
Watch(
    key="grid_cursor",
    address=0x805A1234,
    kind="u8",
    valid=lambda v: v < 50,
    label=lambda v: GAME_NAMES.get(v),
)
```

That is the whole integration step. The scanner and the runtime share `DolphinLink`, so
an address that behaves in `watch` behaves in the companion.

## Layout

```
run.py                     launcher
rhfaccess/dolphin.py       hooking, reconnect, validated big-endian reads
rhfaccess/probes.py        confidence-gating engine (stability, dedupe, forgetting)
rhfaccess/speech.py        Tolk/NVDA output with interrupt policy
rhfaccess/games/rhf.py     RHF specifics: game IDs, Watch definitions
rhfaccess/app.py           poll loop
tools/scan.py              read-only memory scanner
```

`games/` is a package so the same engine can host other Dolphin titles later. Only
`rhf.py` is game-specific; everything else is reusable.
