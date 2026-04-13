"""Tests for three-tier memory (Core / Topic / Event Log) and Active Topic Context."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nanobot.agent.context import ContextBuilder
from nanobot.agent.memory import MemoryStore
from nanobot.providers.base import LLMResponse, ToolCallRequest


def _save_memory_response() -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCallRequest(
                id="call_1",
                name="save_memory",
                arguments={
                    "history_entry": "[2026-01-01] ok",
                    "memory_update": "# Mem\nok",
                },
            )
        ],
    )


def test_list_topics_empty_when_topics_dir_missing(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    assert not store.topics_dir.exists()
    assert store.list_topics() == []


def test_read_topic_missing_returns_empty(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    assert store.read_topic("nope") == ""


def test_write_topic_creates_dir_and_round_trip(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_topic("alpha", "# A\n\nbody")
    assert store.topics_dir.is_dir()
    assert store.read_topic("alpha").startswith("# A")
    topics = store.list_topics()
    assert len(topics) == 1
    assert topics[0]["name"] == "alpha"
    assert "body" in topics[0]["summary"]


def test_get_memory_context_core_only_without_topics(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_long_term("hello core")
    ctx = store.get_memory_context()
    assert "## Core Memory" in ctx
    assert "hello core" in ctx
    assert "Topic Memory Index" not in ctx
    assert "Long-term Memory" not in ctx


def test_get_memory_context_three_topics_index(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_long_term("core")
    for i, name in enumerate(["a", "b", "c"]):
        store.write_topic(name, f"p{i}\nline two")
    ctx = store.get_memory_context()
    assert ctx.count("- **a**:") == 1
    assert ctx.count("- **b**:") == 1
    assert ctx.count("- **c**:") == 1
    assert "Use read_file on memory/topics/<name>.md for full details." in ctx


def test_build_messages_injects_active_topic_on_keyword(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("# Core\n\nx", encoding="utf-8")
    topics = mem / "topics"
    topics.mkdir()
    (topics / "project-ethanbot.md").write_text("# T\n\nDetail here.", encoding="utf-8")

    cb = ContextBuilder(tmp_path)
    msgs = cb.build_messages(history=[], current_message="deploy ethanbot today")
    system = msgs[0]["content"]
    assert "# Active Topic Context" in system
    assert "## project-ethanbot" in system
    assert "Detail here." in system


def test_build_messages_no_active_topic_when_no_keyword_match(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("# Core\n\nx", encoding="utf-8")
    topics = mem / "topics"
    topics.mkdir()
    (topics / "project-ethanbot.md").write_text("# T\n\nDetail.", encoding="utf-8")

    cb = ContextBuilder(tmp_path)
    msgs = cb.build_messages(history=[], current_message="unrelated chat")
    assert "# Active Topic Context" not in msgs[0]["content"]


def test_build_messages_matches_against_last_six_history_strings(tmp_path: Path) -> None:
    mem = tmp_path / "memory"
    mem.mkdir(parents=True)
    (mem / "MEMORY.md").write_text("# Core\n\nx", encoding="utf-8")
    topics = mem / "topics"
    topics.mkdir()
    (topics / "project-foo.md").write_text("# F\n\nbody", encoding="utf-8")

    cb = ContextBuilder(tmp_path)
    history = [{"role": "user", "content": "old"}] * 7
    history[-1] = {"role": "user", "content": "mention foo project"}
    msgs = cb.build_messages(history=history, current_message="hi")
    assert "# Active Topic Context" in msgs[0]["content"]
    assert "## project-foo" in msgs[0]["content"]


@pytest.mark.asyncio
async def test_consolidate_user_prompt_includes_existing_topic_memories(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.write_topic("t1", "alpha\nbeta")
    provider = AsyncMock()
    provider.chat_with_retry = AsyncMock(return_value=_save_memory_response())
    messages = [{"role": "user", "content": "m", "timestamp": "2026-01-01 00:00"}]

    await store.consolidate(messages, provider, "m")

    kwargs = provider.chat_with_retry.call_args.kwargs
    user_msg = kwargs["messages"][1]["content"]
    assert "## Existing Topic Memories" in user_msg
    assert "t1:" in user_msg
    assert "Do not duplicate topic-specific details already covered above." in user_msg
