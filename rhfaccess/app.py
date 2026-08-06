"""Main poll loop."""

from __future__ import annotations

import argparse
import time

from . import APP_NAME, __version__
from .dolphin import DolphinLink
from .games import rhf
from .probes import ProbeEngine
from .speech import Speech

# MEM2 can be unreadable for a second or two around a boot or a re-attach with
# nothing wrong, so this waits before saying so. Past that it is a real fault
# and the user needs to be told, because the companion does not stop working —
# it goes half-blind, which is much harder to notice by ear.
MEM2_WARN_AFTER = 4.0

MEM2_LOST = ("Cannot read the game's memory. Menu names and on-screen text "
             "will stay silent. Restarting Dolphin usually fixes this.")
MEM2_BACK = "Game memory readable again."


def set_window_title(title: str) -> None:
    """Name the console window so the screen reader announces it correctly."""
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        print(f"\33]0;{title}\a", end="", flush=True)


def run(poll_hz: float, speak: bool, echo: bool) -> int:
    set_window_title(APP_NAME)
    print(f"{APP_NAME} v{__version__}")

    speech = Speech(enabled=speak, echo=echo)
    if speech.available:
        print(f"Screen reader: {speech.screen_reader or 'unknown (Tolk loaded)'}")
    else:
        print("Screen reader: none — printing to console only.")

    link = DolphinLink()
    engine = ProbeEngine(rhf.build_probes())
    period = 1.0 / poll_hz
    print("Waiting for Dolphin... (Ctrl+C to quit)")

    mem2_down_since = 0.0
    mem2_warned = False

    try:
        while True:
            start = time.monotonic()

            connected = link.ensure_connected()
            changed = link.note_connection_change()
            if changed is False:
                engine.reset()
                mem2_down_since, mem2_warned = 0.0, False
                speech.say("Lost connection to Dolphin.", interrupt=True)
            elif changed is True:
                speech.say("Hooked into Dolphin.", interrupt=True)

            if connected:
                # Being hooked is not the same as being able to see. MEM1 keeps
                # working through dolphin-memory-engine whatever happens to the
                # raw backend, so the companion stays connected, keeps naming
                # the game and keeps following the cursor while every screen
                # that reads text — which is most of them — has gone dark.
                if link.mem2_available:
                    if mem2_warned:
                        speech.say(MEM2_BACK, interrupt=True)
                    mem2_down_since, mem2_warned = 0.0, False
                else:
                    if not mem2_down_since:
                        mem2_down_since = start
                    elif not mem2_warned and start - mem2_down_since >= MEM2_WARN_AFTER:
                        mem2_warned = True
                        speech.say(MEM2_LOST, interrupt=True)

                for utterance in engine.tick(link):
                    speech.say(utterance.text, interrupt=utterance.interrupt)

            elapsed = time.monotonic() - start
            time.sleep(max(0.0, period - elapsed))
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        speech.close()
        link.disconnect()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rhf-access", description=APP_NAME)
    parser.add_argument("--hz", type=float, default=30.0,
                        help="polls per second (default: 30)")
    parser.add_argument("--no-speech", action="store_true",
                        help="skip Tolk and print to the console only")
    parser.add_argument("--quiet", action="store_true",
                        help="do not echo spoken lines to the console")
    args = parser.parse_args(argv)
    return run(poll_hz=max(1.0, args.hz), speak=not args.no_speech, echo=not args.quiet)
