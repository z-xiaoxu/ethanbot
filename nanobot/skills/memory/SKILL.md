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

## Skill Proposals

Pending skill proposals live as JSON files under `memory/skill-proposals/` (`prop-*.json`). Each file follows a fixed schema (`id`, `type` `create` or `patch`, `status`, `skill_name`, `reason`, `proposed_content`, `source_threads`, `created_at`, optional `diff_description`, `original_content`, `target_skill`).

### Per-turn rules

- Mention **at most one** pending proposal per user-visible turn.
- **Prioritize** `type: "patch"` over `type: "create"` when both exist.
- **Never** modify, create, or overwrite any file under `skills/` (including `SKILL.md`, `GUIDE.md`, and all subfiles) without **explicit user confirmation** in the current conversation. This applies to **all** writes — whether from a proposal, a direct user preference, or your own initiative. Always describe the planned change and ask before writing. Do not treat silence as consent.

### User-facing copy (adapt language to the user)

- **Create:** "I noticed a recurring task pattern: [reason]. Would you like me to create a '[skill_name]' skill?"
- **Patch:** "Last time the '[skill_name]' skill was used we hit an issue; here is a proposed fix: [diff_description]. Would you like me to update the skill?"

### After the user confirms

1. `read_file` the proposal JSON to load the latest fields.
2. For **create**: ensure `skills/{skill_name}/` exists, then `write_file` `skills/{skill_name}/SKILL.md` with `proposed_content` (full file body including frontmatter).
3. For **patch**: replace `skills/{skill_name}/SKILL.md` with `proposed_content` (typically via `write_file` after `read_file` of the current file if you need context).
4. Update the proposal file: set `"status": "accepted"` (preserve other fields) using `write_file` or `edit_file`.

### After the user rejects

- Update the proposal JSON so `"status": "rejected"` and keep a short note in `reason` if helpful; do not modify `skills/**/SKILL.md` for that proposal.
