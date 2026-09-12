---
name: rhf-access-project
description: "The rhf-access companion project — what it is, where it lives, who has access"
metadata: 
  node_type: memory
  type: project
  originSessionId: f0a6dd58-7c21-4213-9575-878bc2c9b1aa
  modified: 2026-07-31T03:50:16.933Z
---

`rhf-access` is an external accessibility companion for Rhythm Heaven Fever (Wii,
disc ID SOME01) running in Dolphin. It reads live game state out of Dolphin's
emulated memory and sends it to screen readers through Prism. Dolphin is never patched
and the disc image is never modified.

Working tree: `C:\Users\adels\Documents\GitHub\rhf-access` (not inside the
Dolphin install, so Dolphin's updater can never touch it).

GitHub: https://github.com/KamiKitsune420/rh-fever-access — **private**. The `gh`
CLI is authenticated as `KamiKitsune420`. Collaborator: `kalahami` (write).
`tsatria03` was invited on 2026-07-27 and the invitation was cancelled on
2026-07-30 at the user's request.

The repo's own CLAUDE.md and README carry the technical detail — architecture,
verified addresses, the menu layout, and the traps. Read those first; they are
kept current deliberately and are more reliable than memory.

See [[rhf-access-working-style]].
