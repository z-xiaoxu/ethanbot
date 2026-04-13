# Long-term Memory

This file stores important information that should persist across sessions. Durable project or theme details may also live in `memory/topics/<name>.md` after Topic Synthesis.

<!-- nanobot-topic-meta: last_synth_iso=1970-01-01T00:00:00Z -->

## Behavioral Observations

> **For the memory consolidation agent (memory_update)**: When consolidating long-term memory, the full content of this file is included in the merge prompt. You must **preserve this section's structure and headings**. When adding a new observable fact, append a short observation to the `### Pending` list using the format shown below. Do not delete entries under `### Synthesized` unless the user explicitly requests cleanup.
>
> **`### Pending` limit**: Maximum **10** `- ` list items. When full, apply **rotation/compression** before adding new entries: (1) merge semantically duplicate or similar-strength entries into one; (2) drop the weakest, oldest, or already-superseded observations; (3) if still over limit, remove the oldest entry by FIFO and note the compression in the merge summary.

### Pending

- [USER][thread:example-session-key] (example: user prefers dark mode, primarily uses Python)
- [SOUL][thread:example-session-key] (example: assistant replies tend to be short, rarely uses examples)

### Synthesized

- (Consumed observations are archived here by the Heartbeat "Profile Synthesis" task after writing to `USER.md` / `SOUL.md` dynamic sections; optional)

*This file is automatically updated by nanobot when important information should be remembered.*

<!-- nanobot-profile-meta: last_synth_iso=1970-01-01T00:00:00Z pending_count=0 -->
