---
name: memory
description: Three-tier memory (Core, Topic, Event Log) with grep-based recall.
always: true
---

# Memory

## Structure (three tiers)

- **Core** — `memory/MEMORY.md`. Always loaded. Holds identity, preferences, key relationships, and behavioral notes. Not a fixed template except for `## Behavioral Observations`.
- **Topic** — `memory/topics/<name>.md`. A **Topic Memory Index** (names + short summaries) is always in context; full files load when keywords match recent messages or when you `read_file` them. Use for project context, deep dives, and ongoing themes.
- **Event Log** — `memory/HISTORY.md`. Append-only timeline. **Not** loaded into context. Search with grep-style tools or targeted reads.

## Behavioral Observations

`## Behavioral Observations` contains `### Pending` / `### Synthesized`. The memory consolidation agent must **preserve this structure**. New observations go to `### Pending` as `- [USER][thread:...]` or `- [SOUL][thread:...]` (max 10 items; see that section for rotation). See `memory/MEMORY.md` and `HEARTBEAT.md` for **Profile Synthesis** and **Topic Synthesis**.

## Using Topic Memory

Before answering non-trivial questions, check the Topic Memory Index in context:

1. If the conversation likely relates to any listed topic, **`read_file`** that topic **first**.
2. When unsure which topic applies, **`read_file`** the most likely candidate.
3. If several topics could apply, load **all** of them.

## Search Past Events

Choose the search method based on file size:

- Small `memory/HISTORY.md`: use `read_file`, then search in-memory
- Large or long-lived `memory/HISTORY.md`: use the `exec` tool for targeted search

Examples:
- **Linux/macOS:** `grep -i "keyword" memory/HISTORY.md`
- **Windows:** `findstr /i "keyword" memory\HISTORY.md`
- **Cross-platform Python:** `python -c "from pathlib import Path; text = Path('memory/HISTORY.md').read_text(encoding='utf-8'); print('\n'.join([l for l in text.splitlines() if 'keyword' in l.lower()][-20:]))"`

Prefer targeted command-line search for large history files.

## When to Update MEMORY.md

Write important facts immediately using `edit_file` or `write_file`:

- User preferences ("I prefer dark mode")
- Cross-cutting relationships ("Alice is the project lead")
- Notes that belong in Core rather than a single project file

## Auto-consolidation

Old conversations are automatically summarized and appended to `HISTORY.md` when the session grows large. Long-term facts are extracted into `MEMORY.md`. Topic Synthesis (Heartbeat) can later move bulky project narrative into `memory/topics/`. You do not need to manage consolidation yourself.
