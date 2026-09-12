"""Speech and braille output through Prism.

Two rules matter for a rhythm game:

1. Cursor movement must interrupt. If you flick down four menu entries, you
   want to hear the fourth, not a backlog of three.
2. Nothing may be said twice in a row by accident. Probes poll many times a
   second; only *changes* get spoken, and identical consecutive text is
   dropped.
"""

from __future__ import annotations

import sys
import time
from typing import Optional

import prism


class Speech:
    """Screen-reader output with de-duplication and an interrupt policy."""

    def __init__(self, enabled: bool = True, echo: bool = True,
                 repeat_suppress_seconds: float = 0.0) -> None:
        self.echo = echo
        self.repeat_suppress_seconds = repeat_suppress_seconds
        self._last_text: Optional[str] = None
        self._last_time = 0.0
        self._context: Optional[prism.Context] = None
        self._backend: Optional[prism.Backend] = None
        self.screen_reader: Optional[str] = None

        if enabled:
            try:
                self._context = prism.Context()
                self._backend = self._context.acquire_best()
                if not self._backend.features.supports_output:
                    raise RuntimeError(
                        f"{self._backend.name} does not support Prism output"
                    )
                self.screen_reader = self._backend.name or None
            except Exception as exc:
                print(f"[speech] Prism unavailable ({exc}); falling back to console.",
                      file=sys.stderr)
                self._backend = None
                self._context = None

    @property
    def available(self) -> bool:
        return self._backend is not None

    def say(self, text: str, interrupt: bool = False) -> None:
        text = (text or "").strip()
        if not text:
            return

        now = time.monotonic()
        if text == self._last_text and (
            self.repeat_suppress_seconds <= 0
            or now - self._last_time < self.repeat_suppress_seconds
        ):
            return
        self._last_text = text
        self._last_time = now

        if self.echo:
            print(text, flush=True)
        if self._backend is not None:
            try:
                self._backend.output(text, interrupt=interrupt)
            except Exception as exc:
                print(f"[speech] output failed: {exc}", file=sys.stderr)

    def silence(self) -> None:
        if self._backend is not None:
            try:
                self._backend.stop()
            except Exception:
                pass

    def force_repeat(self) -> None:
        """Clear the de-dupe memory so the next identical line is spoken."""
        self._last_text = None

    def close(self) -> None:
        if self._backend is not None:
            try:
                self._backend.stop()
            except Exception:
                pass
        self._backend = None
        self._context = None
