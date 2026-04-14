"""Background skill patch proposal analysis (AgentLoop)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.agent.runner import AgentRunResult
from nanobot.providers.base import LLMResponse, ToolCallRequest


@pytest.mark.asyncio
async def test_analyze_skill_usage_writes_pending_patch_proposal(tmp_path) -> None:
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="",
        tool_calls=[ToolCallRequest(
            id="t1",
            name="submit_skill_patch_analysis",
            arguments={
                "should_patch": True,
                "reason": "Instructions omitted the retry flag.",
                "diff_description": "Document retry after transient errors.",
                "proposed_content": "---\nname: demo\n---\n# Patched\n",
            },
        )],
        usage={},
    ))

    skill_dir = tmp_path / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n# Old\n", encoding="utf-8")

    with patch("nanobot.agent.loop.ContextBuilder"), \
         patch("nanobot.agent.loop.SessionManager"), \
         patch("nanobot.agent.loop.SubagentManager") as mock_submgr:
        mock_submgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)

    tool_events = [
        {"name": "read_file", "status": "ok", "detail": "lines", "path": "skills/demo-skill/SKILL.md"},
        {"name": "exec", "status": "error", "detail": "Error: timeout"},
        {"name": "read_file", "status": "ok", "detail": "retry ok", "path": "notes.txt"},
    ]
    result = AgentRunResult(
        final_content="done",
        messages=[],
        tool_events=tool_events,
    )
    await loop._analyze_skill_usage(result, "cli:direct")

    prop_dir = tmp_path / "memory" / "skill-proposals"
    files = sorted(prop_dir.glob("prop-*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text(encoding="utf-8"))
    assert data["type"] == "patch"
    assert data["status"] == "pending"
    assert data["skill_name"] == "demo-skill"
    assert data["source_threads"] == ["cli:direct"]


@pytest.mark.asyncio
async def test_analyze_skill_usage_skips_without_error_recovery(tmp_path) -> None:
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock()

    with patch("nanobot.agent.loop.ContextBuilder"), \
         patch("nanobot.agent.loop.SessionManager"), \
         patch("nanobot.agent.loop.SubagentManager") as mock_submgr:
        mock_submgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)

    skill_dir = tmp_path / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("x", encoding="utf-8")

    tool_events = [
        {"name": "read_file", "status": "ok", "detail": "x", "path": "skills/demo-skill/SKILL.md"},
        {"name": "exec", "status": "error", "detail": "err"},
    ]
    result = AgentRunResult(final_content="done", messages=[], tool_events=tool_events)
    await loop._analyze_skill_usage(result, "cli:direct")

    provider.chat_with_retry.assert_not_called()
    prop_dir = tmp_path / "memory" / "skill-proposals"
    assert not prop_dir.exists() or not list(prop_dir.glob("prop-*.json"))


@pytest.mark.asyncio
async def test_analyze_skill_usage_skips_when_llm_declines(tmp_path) -> None:
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="",
        tool_calls=[ToolCallRequest(
            id="t1",
            name="submit_skill_patch_analysis",
            arguments={"should_patch": False, "reason": "no change needed"},
        )],
        usage={},
    ))

    skill_dir = tmp_path / "skills" / "demo-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("x", encoding="utf-8")

    with patch("nanobot.agent.loop.ContextBuilder"), \
         patch("nanobot.agent.loop.SessionManager"), \
         patch("nanobot.agent.loop.SubagentManager") as mock_submgr:
        mock_submgr.return_value.cancel_by_session = AsyncMock(return_value=0)
        loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path)

    tool_events = [
        {"name": "read_file", "status": "ok", "detail": "x", "path": "skills/demo-skill/SKILL.md"},
        {"name": "exec", "status": "error", "detail": "err"},
        {"name": "list_dir", "status": "ok", "detail": "ok"},
    ]
    result = AgentRunResult(final_content="done", messages=[], tool_events=tool_events)
    await loop._analyze_skill_usage(result, "cli:direct")

    prop_dir = tmp_path / "memory" / "skill-proposals"
    assert not prop_dir.exists() or not list(prop_dir.glob("prop-*.json"))


def test_find_skill_patch_signal_prefers_latest_qualifying_read() -> None:
    from nanobot.agent.loop import AgentLoop

    events = [
        {"name": "read_file", "status": "ok", "detail": "a", "path": "skills/old/SKILL.md"},
        {"name": "read_file", "status": "ok", "detail": "b", "path": "skills/new/SKILL.md"},
        {"name": "web_search", "status": "error", "detail": "e"},
        {"name": "read_file", "status": "ok", "detail": "c", "path": "x.txt"},
    ]
    got = AgentLoop._find_skill_patch_signal(events)
    assert got is not None
    assert got[0] == "new"
