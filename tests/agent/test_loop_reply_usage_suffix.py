"""Optional usage suffix on assistant replies."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMResponse


@pytest.mark.asyncio
async def test_suffix_disabled_by_default(tmp_path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    provider.generation = GenerationSettings(max_tokens=4096, show_usage_in_reply=False)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="hello", tool_calls=[], usage={"prompt_tokens": 3, "completion_tokens": 1},
    ))
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="m")
    loop.tools.get_definitions = MagicMock(return_value=[])

    out = await loop.process_direct("hi", session_key="cli:sfx")
    assert out is not None
    assert out.content == "hello"
    assert "prompt +" not in out.content


@pytest.mark.asyncio
async def test_suffix_when_enabled_is_italic_markdown(tmp_path) -> None:
    provider = MagicMock()
    provider.get_default_model.return_value = "m"
    provider.generation = GenerationSettings(max_tokens=4096, show_usage_in_reply=True)
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="hello", tool_calls=[], usage={"prompt_tokens": 3, "completion_tokens": 1},
    ))
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="m")
    loop.tools.get_definitions = MagicMock(return_value=[])

    out = await loop.process_direct("hi", session_key="cli:sfx2")
    assert out is not None
    assert out.content.startswith("hello")
    assert out.content.rstrip().endswith("_")
    assert "_3 prompt + 1 completion tokens · ~" in out.content
