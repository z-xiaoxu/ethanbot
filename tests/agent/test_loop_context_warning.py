"""AgentLoop context window warnings (80% / 90%)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ChannelsConfig
from nanobot.providers.base import GenerationSettings, LLMResponse


def _loop(tmp_path, *, ctx_tokens: int, send_progress: bool = True) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    provider.generation = GenerationSettings(max_tokens=1000)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))
    provider.chat_stream_with_retry = AsyncMock(return_value=LLMResponse(content="ok", tool_calls=[]))

    ch = ChannelsConfig(send_progress=send_progress)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="m",
        context_window_tokens=ctx_tokens,
        channels_config=ch,
    )
    loop.tools.get_definitions = MagicMock(return_value=[])
    return loop


@pytest.mark.asyncio
async def test_eighty_percent_info_once_until_consolidation_advances(tmp_path) -> None:
    loop = _loop(tmp_path, ctx_tokens=10_000)
    session = loop.sessions.get_or_create("cli:w")
    bus = loop.bus
    bus.publish_outbound = AsyncMock()

    loop.memory_consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(8200, "t"))

    await loop._check_context_warning(session, channel="cli", chat_id="w", metadata={})
    assert bus.publish_outbound.await_count == 1
    assert session.metadata.get("_ctx_warned_0") == 1

    await loop._check_context_warning(session, channel="cli", chat_id="w", metadata={})
    assert bus.publish_outbound.await_count == 1

    session.last_consolidated = 3
    await loop._check_context_warning(session, channel="cli", chat_id="w", metadata={})
    assert bus.publish_outbound.await_count == 2


@pytest.mark.asyncio
async def test_ninety_percent_warning_suggests_new(tmp_path) -> None:
    loop = _loop(tmp_path, ctx_tokens=10_000)
    session = loop.sessions.get_or_create("cli:w2")
    loop.bus.publish_outbound = AsyncMock()

    loop.memory_consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(9200, "t"))

    await loop._check_context_warning(session, channel="cli", chat_id="w2", metadata={})
    call = loop.bus.publish_outbound.await_args
    assert "/new" in (call[0][0].content or "")


@pytest.mark.asyncio
async def test_send_progress_false_skips_publish(tmp_path) -> None:
    loop = _loop(tmp_path, ctx_tokens=10_000, send_progress=False)
    session = loop.sessions.get_or_create("cli:w3")
    loop.bus.publish_outbound = AsyncMock()
    loop.memory_consolidator.estimate_session_prompt_tokens = MagicMock(return_value=(8500, "t"))

    await loop._check_context_warning(session, channel="cli", chat_id="w3", metadata={})

    loop.bus.publish_outbound.assert_not_awaited()
