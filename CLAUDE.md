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

Windows, Python 3.12. `pip install -r requirements.txt` (dolphin-memory-engine,
cytolk, numpy; Pillow is needed for `tools/sweep.py`).

```
python run.py                     # the companion; --no-speech for console only
python tools/step.py auto 12 S W  # automated memory scan (see below)
python tools/sweep.py             # drive the menu, log + screenshot each stop
```

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
| `tools/scan.py` | Memory scanner core (numpy-vectorised, read-only) |
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

- `0x80320404` — game-select cursor index, u8, mirrored at `+1`. `0xFF` means
  no valid selection.
- `0x80320430` — pointer to the selected entry, array of `0x50`-byte structs.
- `0x8032A5C0` — menu state, u32. 1 = grid, 3 = game info card open.

## Reading on-screen text (start here for any new screen)

The UI is NW4R layouts. Every text box is an object holding its own ASCII pane
name (`T_game_title_00`, `T_exposition_00`) with a pointer to its live UTF-16BE
string at name + `0x1C`. Ask for text **by pane name** — do not go hunting for
individual buffers, which is slow and yields addresses that move.

`panes.PaneIndex.scan()` with no arguments returns every live text pane. That is
the first thing to run when adding support for a new screen: it shows what the
screen exposes and what the names are.

**A pane keeps its last string after its screen closes**, and nothing in the pane
indicates visibility. Always gate on game state (e.g. `0x8032A5C0` for the info
card) or you will announce stale text.

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

## Style

Match the existing code: type hints, `Optional` returns for anything that can
fail, comments that explain *why* a check exists rather than restating it. When
a fact is provisional, say so where the code is — and keep the README's
"Provisional" section honest, because it is the handover between sessions.
