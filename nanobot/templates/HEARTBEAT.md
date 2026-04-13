# Heartbeat Tasks

This file is checked every 30 minutes by your nanobot agent.
Add tasks below that you want the agent to work on periodically.

If this file has no tasks (only headers and comments), the agent will skip the heartbeat.

## System Tasks

<!-- Built-in background tasks. Results are silently processed — no user notification. -->

### Profile Synthesis (USER.md / SOUL.md dynamic sections)

Synthesize behavioral observations from `memory/MEMORY.md` `### Pending` into the dynamic sections of `USER.md` and `SOUL.md`. Always use `read_file` / `edit_file` to stay within section scope — never use `write_file` for these files. Do not modify fixed sections in `SOUL.md` (`## Personality`, `## Values`) or anything outside `## Dynamic Profile` in `USER.md`.

1. **`read_file`** `memory/MEMORY.md` and parse the trailing comment `<!-- nanobot-profile-meta: last_synth_iso=<UTC ISO8601> ... -->` to extract `last_synth_iso` (required).
2. Count the `- ` list items under `### Pending` — this is the source of truth for `pending` (you may cross-check against `pending_count` in the meta comment).
3. **Skip this task if** either condition is true: `pending < 3`, or the current UTC time is less than **7 days** after `last_synth_iso`. Write the skip reason in your output and **stop** — do not write to `USER.md`, `SOUL.md`, or the meta comment.
4. If both checks pass: `read_file` `USER.md`, `SOUL.md`, and relevant parts of `memory/MEMORY.md`. Combine `### Pending` observations with long-term fact sections, then use `edit_file` to **replace the entire block**:
   - In `USER.md`: from `## Dynamic Profile` to the next sibling `## ` heading or end of file.
   - In `SOUL.md`: from `## Dynamic Style Preferences` to the next sibling `## ` heading or end of file.
5. **SOUL write threshold**: Before writing to `## Dynamic Style Preferences`, check `[SOUL]`-tagged entries in `### Pending` and their `[thread:...]` anchors. Only write a conclusion for a style dimension when there are **>= 2 observations from different threads** with **no contradictions**. Otherwise, keep the placeholder or previous text and note "insufficient evidence" in your reasoning.
6. After successful synthesis: use `edit_file` to update the trailing meta in `memory/MEMORY.md` to `<!-- nanobot-profile-meta: last_synth_iso=<current UTC time> pending_count=<new count, optional> -->`. Move or summarize consumed observations from `### Pending` to `### Synthesized`.
7. Verify that you did not alter fixed sections in `SOUL.md` or content outside `## Dynamic Profile` in `USER.md`. If you skipped, do not clear the `### Pending` list.

### Topic Synthesis (MEMORY.md → memory/topics/)

Move project- or theme-specific narrative out of Core memory into `memory/topics/*.md`, keeping identity and behavioral notes in `MEMORY.md`. Use `read_file`, `list_dir`, `write_file`, and `edit_file` only — do not invent new tools.

1. **`read_file`** `memory/MEMORY.md` and parse `<!-- nanobot-topic-meta: last_synth_iso=<UTC ISO8601> -->` (required).
2. **Skip this task if** either is true: current UTC time is less than **24 hours** after `last_synth_iso`, **or** the Markdown **above** `## Behavioral Observations` (excluding the topic meta comment line) is fewer than **200 characters** of substantive text. Log the skip reason and **stop** — do not edit `MEMORY.md` or topics.
3. **`list_dir`** `memory/topics/` (create the directory only if you will write a topic file).
4. Analyze everything **above** `## Behavioral Observations`: keep user-level facts in `MEMORY.md`; move project technical decisions and progress to `memory/topics/project-<name>.md`; route ongoing non-project tracking to `memory/topics/<name>.md` (e.g. `daily.md`). For each target topic that already exists, **`read_file`** it first and **merge by meaning** (do not blindly overwrite).
5. **`write_file`** or **`edit_file`** topic files after merge. Then **`edit_file`** `memory/MEMORY.md` to remove sections you migrated. **Never** modify `## Behavioral Observations` or anything below it.
6. After success, update the trailing `<!-- nanobot-topic-meta: last_synth_iso=<current UTC ISO8601> -->` in `memory/MEMORY.md`.
7. Verify Behavioral Observations and below are unchanged. If you skipped at step 2, do not update the topic meta.

## Active Tasks

<!-- Add your periodic tasks below this line. You will be notified of results. -->

## Completed

<!-- Move completed tasks here or delete them -->
