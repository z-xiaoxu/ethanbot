"""cmd_new clears session usage counters and context-warning metadata."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nanobot.bus.events import InboundMessage
from nanobot.command.builtin import cmd_new
from nanobot.command.router import CommandContext
from nanobot.providers.base import GenerationSettings


@pytest.mark.asyncio
async def test_cmd_new_clears_session_usage_and_ctx_warn_keys(tmp_path) -> None:
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    provider.generation = GenerationSettings(max_tokens=4096)

    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="m")
    session = loop.sessions.get_or_create("cli:unit")
    session.metadata["_ctx_warned_0"] = 1
    session.metadata["_ctx_warned_1"] = 2
    session.metadata["other"] = "keep"
    loop._session_usage["cli:unit"] = {"prompt_tokens": 9, "completion_tokens": 1, "llm_calls": 2}

    msg = InboundMessage(channel="cli", sender_id="u", chat_id="unit", content="/new")
    ctx = CommandContext(msg=msg, session=session, key="cli:unit", raw="/new", loop=loop)

    await cmd_new(ctx)

    assert "cli:unit" not in loop._session_usage
    assert "_ctx_warned_0" not in session.metadata
    assert "_ctx_warned_1" not in session.metadata
    assert session.metadata.get("other") == "keep"
