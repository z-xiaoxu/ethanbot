"""Memory system for persistent agent memory."""

from __future__ import annotations

import asyncio
import json
import re
import weakref
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from loguru import logger

from nanobot.utils.helpers import ensure_dir, estimate_message_tokens, estimate_prompt_tokens_chain

if TYPE_CHECKING:
    from nanobot.providers.base import LLMProvider
    from nanobot.session.manager import Session, SessionManager


_SAVE_MEMORY_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save the memory consolidation result to persistent storage.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_entry": {
                        "type": "string",
                        "description": "A paragraph summarizing key events/decisions/topics. "
                        "Start with [YYYY-MM-DD HH:MM]. Include detail useful for grep search.",
                    },
                    "memory_update": {
                        "type": "string",
                        "description": "Full updated long-term memory as markdown. Include all existing "
                        "facts plus new ones. Return unchanged if nothing new.",
                    },
                },
                "required": ["history_entry", "memory_update"],
            },
        },
    }
]


def _ensure_text(value: Any) -> str:
    """Normalize tool-call payload values to text for file storage."""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _normalize_save_memory_args(args: Any) -> dict[str, Any] | None:
    """Normalize provider tool-call arguments to the expected dict shape."""
    if isinstance(args, str):
        args = json.loads(args)
    if isinstance(args, list):
        return args[0] if args and isinstance(args[0], dict) else None
    return args if isinstance(args, dict) else None

_TOOL_CHOICE_ERROR_MARKERS = (
    "tool_choice",
    "toolchoice",
    "does not support",
    'should be ["none", "auto"]',
)


def _is_tool_choice_unsupported(content: str | None) -> bool:
    """Detect provider errors caused by forced tool_choice being unsupported."""
    text = (content or "").lower()
    return any(m in text for m in _TOOL_CHOICE_ERROR_MARKERS)


_BO_HEADING = "## Behavioral Observations"
_TOPIC_META_RE = re.compile(r"<!--\s*nanobot-topic-meta:.*?-->")
_PROFILE_META_RE = re.compile(r"<!--\s*nanobot-profile-meta:.*?-->")
_DEFAULT_TOPIC_META = "<!-- nanobot-topic-meta: last_synth_iso=1970-01-01T00:00:00Z -->"
_DEFAULT_PROFILE_META = (
    "<!-- nanobot-profile-meta: last_synth_iso=1970-01-01T00:00:00Z pending_count=0 -->"
)
_DEFAULT_BO_SECTION = """\
## Behavioral Observations

> **For the memory consolidation agent (memory_update)**: When consolidating long-term memory, \
the full content of this file is included in the merge prompt. You must **preserve this section's \
structure and headings**. When adding a new observable fact, append a short observation to the \
`### Pending` list using the format shown below. Do not delete entries under `### Synthesized` \
unless the user explicitly requests cleanup.
>
> **`### Pending` limit**: Maximum **10** `- ` list items. When full, apply \
**rotation/compression** before adding new entries: (1) merge semantically duplicate or \
similar-strength entries into one; (2) drop the weakest, oldest, or already-superseded \
observations; (3) if still over limit, remove the oldest entry by FIFO and note the compression \
in the merge summary.

### Pending

### Synthesized

*This file is automatically updated by nanobot when important information should be remembered.*
"""


def _ensure_structural_sections(update: str, current: str) -> str:
    """Guarantee that Behavioral Observations and meta comments survive consolidation.

    If the LLM dropped these sections, restore them from *current* memory or fall back
    to built-in defaults so that Heartbeat Profile/Topic Synthesis can always run.
    """
    # --- Behavioral Observations ---
    if _BO_HEADING in current:
        bo_idx = current.index(_BO_HEADING)
        current_bo = current[bo_idx:]
    else:
        current_bo = None

    if _BO_HEADING not in update:
        preserved = current_bo or (_DEFAULT_BO_SECTION + "\n" + _DEFAULT_PROFILE_META)
        update = update.rstrip() + "\n\n" + preserved
    elif current_bo:
        # LLM kept the heading but may have mangled the body — replace with original
        update_bo_idx = update.index(_BO_HEADING)
        update = update[:update_bo_idx].rstrip() + "\n\n" + current_bo

    # --- topic meta ---
    if not _TOPIC_META_RE.search(update):
        current_match = _TOPIC_META_RE.search(current)
        meta = current_match.group(0) if current_match else _DEFAULT_TOPIC_META
        # Insert just before Behavioral Observations
        if _BO_HEADING in update:
            bo_pos = update.index(_BO_HEADING)
            update = update[:bo_pos].rstrip() + "\n\n" + meta + "\n\n" + update[bo_pos:]
        else:
            update = update.rstrip() + "\n\n" + meta

    # --- profile meta ---
    if not _PROFILE_META_RE.search(update):
        current_match = _PROFILE_META_RE.search(current)
        meta = current_match.group(0) if current_match else _DEFAULT_PROFILE_META
        update = update.rstrip() + "\n" + meta + "\n"

    return update


class MemoryStore:
    """Three-tier memory: Core (MEMORY.md), Topic (memory/topics/*.md), Event Log (HISTORY.md)."""

    _MAX_FAILURES_BEFORE_RAW_ARCHIVE = 3

    def __init__(self, workspace: Path):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.memory_file = self.memory_dir / "MEMORY.md"
        self.history_file = self.memory_dir / "HISTORY.md"
        self.topics_dir = self.memory_dir / "topics"
        self._consecutive_failures = 0

    def read_long_term(self) -> str:
        if self.memory_file.exists():
            return self.memory_file.read_text(encoding="utf-8")
        return ""

    def write_long_term(self, content: str) -> None:
        self.memory_file.write_text(content, encoding="utf-8")

    def append_history(self, entry: str) -> None:
        with open(self.history_file, "a", encoding="utf-8") as f:
            f.write(entry.rstrip() + "\n\n")

    def list_topics(self) -> list[dict[str, str]]:
        """List topic file stems and short summaries (first 5 non-heading body lines)."""
        if not self.topics_dir.exists():
            return []
        result: list[dict[str, str]] = []
        for f in sorted(self.topics_dir.glob("*.md")):
            text = f.read_text(encoding="utf-8")
            lines = [
                l.strip("- ").strip()
                for l in text.splitlines()
                if l.strip() and not l.startswith("#")
            ]
            summary = "; ".join(lines[:5])
            result.append({"name": f.stem, "summary": summary})
        return result

    def read_topic(self, name: str) -> str:
        path = self.topics_dir / f"{name}.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def write_topic(self, name: str, content: str) -> None:
        ensure_dir(self.topics_dir)
        (self.topics_dir / f"{name}.md").write_text(content, encoding="utf-8")

    def get_memory_context(self) -> str:
        parts: list[str] = []
        long_term = self.read_long_term()
        if long_term:
            parts.append(f"## Core Memory\n{long_term}")

        topics = self.list_topics()
        if topics:
            index_lines = [f"- **{t['name']}**: {t['summary']}" for t in topics]
            parts.append(
                "## Topic Memory Index\n"
                + "\n".join(index_lines)
                + "\n\nUse read_file on memory/topics/<name>.md for full details."
            )
        return "\n\n".join(parts)

    @staticmethod
    def _format_messages(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = f" [tools: {', '.join(message['tools_used'])}]" if message.get("tools_used") else ""
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] {message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    async def consolidate(
        self,
        messages: list[dict],
        provider: LLMProvider,
        model: str,
    ) -> bool:
        """Consolidate the provided message chunk into MEMORY.md + HISTORY.md."""
        if not messages:
            return True

        current_memory = self.read_long_term()
        topics = self.list_topics()
        topic_hint = ""
        if topics:
            hint_lines = [f"- {t['name']}: {t['summary']}" for t in topics]
            topic_hint = (
                "\n\n## Existing Topic Memories\n"
                + "\n".join(hint_lines)
                + "\n\nDo not duplicate topic-specific details already covered above."
            )

        prompt = f"""Process this conversation and call the save_memory tool with your consolidation.

## Current Long-term Memory
{current_memory or "(empty)"}{topic_hint}

## Conversation to Process
{self._format_messages(messages)}"""

        system_parts = [
            "You are a memory consolidation agent. Call the save_memory tool with your consolidation of the conversation.",
            "Organize MEMORY.md with clear project or theme sections where appropriate.",
            "Keep user-level facts (identity, preferences, relationships) separate from project-specific technical details.",
            "Use descriptive headings so recurring themes can be identified later.",
            "CRITICAL: You MUST preserve these structural elements EXACTLY as they appear in the current memory:",
            "1. The `## Behavioral Observations` section and everything below it"
            " (including `### Pending`, `### Synthesized`, blockquotes, and all HTML comments)"
            " must be kept at the END of the file, unchanged.",
            "2. All HTML comments (`<!-- ... -->`) must be preserved verbatim — they contain"
            " metadata used by other subsystems.",
            "3. Only update content ABOVE `## Behavioral Observations`."
            " If that section does not exist in the current memory, do NOT create it"
            " — the system will add it automatically.",
        ]
        system_content = "\n".join(system_parts)

        chat_messages = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": prompt},
        ]

        try:
            forced = {"type": "function", "function": {"name": "save_memory"}}
            response = await provider.chat_with_retry(
                messages=chat_messages,
                tools=_SAVE_MEMORY_TOOL,
                model=model,
                tool_choice=forced,
            )

            if response.finish_reason == "error" and _is_tool_choice_unsupported(
                response.content
            ):
                logger.warning("Forced tool_choice unsupported, retrying with auto")
                response = await provider.chat_with_retry(
                    messages=chat_messages,
                    tools=_SAVE_MEMORY_TOOL,
                    model=model,
                    tool_choice="auto",
                )

            if not response.has_tool_calls:
                logger.warning(
                    "Memory consolidation: LLM did not call save_memory "
                    "(finish_reason={}, content_len={}, content_preview={})",
                    response.finish_reason,
                    len(response.content or ""),
                    (response.content or "")[:200],
                )
                return self._fail_or_raw_archive(messages)

            args = _normalize_save_memory_args(response.tool_calls[0].arguments)
            if args is None:
                logger.warning("Memory consolidation: unexpected save_memory arguments")
                return self._fail_or_raw_archive(messages)

            if "history_entry" not in args or "memory_update" not in args:
                logger.warning("Memory consolidation: save_memory payload missing required fields")
                return self._fail_or_raw_archive(messages)

            entry = args["history_entry"]
            update = args["memory_update"]

            if entry is None or update is None:
                logger.warning("Memory consolidation: save_memory payload contains null required fields")
                return self._fail_or_raw_archive(messages)

            entry = _ensure_text(entry).strip()
            if not entry:
                logger.warning("Memory consolidation: history_entry is empty after normalization")
                return self._fail_or_raw_archive(messages)

            self.append_history(entry)
            update = _ensure_text(update)
            update = _ensure_structural_sections(update, current_memory)
            if update != current_memory:
                self.write_long_term(update)

            self._consecutive_failures = 0
            logger.info("Memory consolidation done for {} messages", len(messages))
            return True
        except Exception:
            logger.exception("Memory consolidation failed")
            return self._fail_or_raw_archive(messages)

    def _fail_or_raw_archive(self, messages: list[dict]) -> bool:
        """Increment failure count; after threshold, raw-archive messages and return True."""
        self._consecutive_failures += 1
        if self._consecutive_failures < self._MAX_FAILURES_BEFORE_RAW_ARCHIVE:
            return False
        self._raw_archive(messages)
        self._consecutive_failures = 0
        return True

    def _raw_archive(self, messages: list[dict]) -> None:
        """Fallback: dump raw messages to HISTORY.md without LLM summarization."""
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.append_history(
            f"[{ts}] [RAW] {len(messages)} messages\n"
            f"{self._format_messages(messages)}"
        )
        logger.warning(
            "Memory consolidation degraded: raw-archived {} messages", len(messages)
        )


class MemoryConsolidator:
    """Owns consolidation policy, locking, and session offset updates."""

    _MAX_CONSOLIDATION_ROUNDS = 5

    _SAFETY_BUFFER = 1024  # extra headroom for tokenizer estimation drift

    def __init__(
        self,
        workspace: Path,
        provider: LLMProvider,
        model: str,
        sessions: SessionManager,
        context_window_tokens: int,
        build_messages: Callable[..., list[dict[str, Any]]],
        get_tool_definitions: Callable[[], list[dict[str, Any]]],
        max_completion_tokens: int = 4096,
    ):
        self.store = MemoryStore(workspace)
        self.provider = provider
        self.model = model
        self.sessions = sessions
        self.context_window_tokens = context_window_tokens
        self.max_completion_tokens = max_completion_tokens
        self._build_messages = build_messages
        self._get_tool_definitions = get_tool_definitions
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """Return the shared consolidation lock for one session."""
        return self._locks.setdefault(session_key, asyncio.Lock())

    async def consolidate_messages(self, messages: list[dict[str, object]]) -> bool:
        """Archive a selected message chunk into persistent memory."""
        return await self.store.consolidate(messages, self.provider, self.model)

    def pick_consolidation_boundary(
        self,
        session: Session,
        tokens_to_remove: int,
    ) -> tuple[int, int] | None:
        """Pick a user-turn boundary that removes enough old prompt tokens."""
        start = session.last_consolidated
        if start >= len(session.messages) or tokens_to_remove <= 0:
            return None

        removed_tokens = 0
        last_boundary: tuple[int, int] | None = None
        for idx in range(start, len(session.messages)):
            message = session.messages[idx]
            if idx > start and message.get("role") == "user":
                last_boundary = (idx, removed_tokens)
                if removed_tokens >= tokens_to_remove:
                    return last_boundary
            removed_tokens += estimate_message_tokens(message)

        return last_boundary

    def estimate_session_prompt_tokens(self, session: Session) -> tuple[int, str]:
        """Estimate current prompt size for the normal session history view."""
        history = session.get_history(max_messages=0)
        channel, chat_id = (session.key.split(":", 1) if ":" in session.key else (None, None))
        probe_messages = self._build_messages(
            history=history,
            current_message="[token-probe]",
            channel=channel,
            chat_id=chat_id,
        )
        return estimate_prompt_tokens_chain(
            self.provider,
            self.model,
            probe_messages,
            self._get_tool_definitions(),
        )

    async def archive_messages(self, messages: list[dict[str, object]]) -> bool:
        """Archive messages with guaranteed persistence (retries until raw-dump fallback)."""
        if not messages:
            return True
        for _ in range(self.store._MAX_FAILURES_BEFORE_RAW_ARCHIVE):
            if await self.consolidate_messages(messages):
                return True
        return True

    async def maybe_consolidate_by_tokens(self, session: Session) -> None:
        """Loop: archive old messages until prompt fits within safe budget.

        The budget reserves space for completion tokens and a safety buffer
        so the LLM request never exceeds the context window.
        """
        if not session.messages or self.context_window_tokens <= 0:
            return

        lock = self.get_lock(session.key)
        async with lock:
            budget = self.context_window_tokens - self.max_completion_tokens - self._SAFETY_BUFFER
            target = budget // 2
            estimated, source = self.estimate_session_prompt_tokens(session)
            if estimated <= 0:
                return
            if estimated < budget:
                logger.debug(
                    "Token consolidation idle {}: {}/{} via {}",
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                )
                return

            for round_num in range(self._MAX_CONSOLIDATION_ROUNDS):
                if estimated <= target:
                    return

                boundary = self.pick_consolidation_boundary(session, max(1, estimated - target))
                if boundary is None:
                    logger.debug(
                        "Token consolidation: no safe boundary for {} (round {})",
                        session.key,
                        round_num,
                    )
                    return

                end_idx = boundary[0]
                chunk = session.messages[session.last_consolidated:end_idx]
                if not chunk:
                    return

                logger.info(
                    "Token consolidation round {} for {}: {}/{} via {}, chunk={} msgs",
                    round_num,
                    session.key,
                    estimated,
                    self.context_window_tokens,
                    source,
                    len(chunk),
                )
                if not await self.consolidate_messages(chunk):
                    return
                session.last_consolidated = end_idx
                self.sessions.save(session)

                estimated, source = self.estimate_session_prompt_tokens(session)
                if estimated <= 0:
                    return
