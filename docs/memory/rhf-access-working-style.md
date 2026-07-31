---
name: rhf-access-working-style
description: How to work with this user on rhf-access — verification duties and when not to touch the game
metadata: 
  node_type: memory
  type: feedback
  originSessionId: f0a6dd58-7c21-4213-9575-878bc2c9b1aa
  modified: 2026-07-31T03:50:44.244Z
---

The user works as a screen reader user and cannot check what is on the game's
screen visually. When something needs reading off the screen, that is my job:
take a screenshot and read it. They verify by listening, or occasionally by
running OCR themselves.

**Why:** it changes who can check what. "It looks right" is never available to
them, so an announcement that is confidently wrong can go unnoticed — which is
why the project's rule is silence over guessing.

**How to apply:**
- Screenshot and read the screen myself rather than asking what is displayed.
- Enlarge the Dolphin window to read it (theirs is small, 174x198 at 681,298 —
  they do not need it visible) and **restore it afterwards**.
- Do not send input to the game without checking state first. Pressing keys
  while they were mid-game interfered with their play, and a lost keyup once
  left the D-pad stuck so the cursor scrolled by itself.
- Run test instances of `run.py` under `timeout` and confirm none survive. Two
  stray copies once talked over each other, and the older one was speaking
  pre-fix names — it read as a mapping bug and was not one.

See [[rhf-access-project]].
