---
name: homeshed-tools
description: Which HomeShed tool to reach for. Use when the user asks to remember or recall something across sessions, has an error that may have been seen before, wants a logic or scheduling puzzle solved exactly, wants a repo checked before it goes public, or wants a quick network or machine check.
---

# Using HomeShed's tools

HomeShed's tools appear as `mcp__plugin_homeshed_homeshed__<tool>` (or `mcp__homeshed__<tool>` if the user added
their own server). If none are listed, use the `homeshed-setup` skill first.

| The user wants | Use |
|---|---|
| Something kept for later sessions ("remember that...") | `memory.remember_fact` with a short category |
| What was decided or learnt before | `memory.recall_relevant` (pass `max_chars` to keep answers short) |
| "Have we seen this error before?" | `bugs.find` with the error text, before retrying a fix |
| A logic, scheduling or constraint puzzle | `reasoning.solve`: the Z3 solver proves the answer instead of guessing |
| Whether a repo is ready to publish | `repo.leak_scan` first (private details and secrets), then `repo.readiness` |
| Whether the docs match the code | `repo.docs_check` |
| API keys pasted into past Claude Code sessions | `secrets.transcript_scan`: it says where each key is and where to rotate it, never the key itself |
| DNS, open ports, or what's exposed on the user's own site | `network.dns.lookup`, `network.port_check`, `network.exposure_check` |
| This machine's details or health | `system.info`, `system.health` |
| The right tool for a job, from plain English | `registry.find` |

Good habits:
- Recall before re-deriving: a quick `memory.recall_relevant` often saves a long investigation.
- Save one fact per call, in plain words, without secrets, tokens or personal details.
- Tools that change things (git writes, Docker, publishing) are off until the user switches them on. Don't ask the
  user to switch one on unless the task needs it, and say what it will be able to do.
- `network.exposure_check` and `network.port_check` are for the user's own machines and sites only.
