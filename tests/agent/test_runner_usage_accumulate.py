"""Runner accumulates usage and llm_calls across iterations."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.providers.base import LLMResponse, ToolCallRequest


@pytest.mark.asyncio
async def test_single_llm_round_accumulates_usage_and_one_call() -> None:
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="ok", tool_calls=[], usage={"prompt_tokens": 10, "completion_tokens": 2},
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        model="m",
        max_iterations=3,
    ))

    assert result.usage["prompt_tokens"] == 10
    assert result.usage["completion_tokens"] == 2
    assert result.usage["llm_calls"] == 1


@pytest.mark.asyncio
async def test_tool_loop_accumulates_across_rounds() -> None:
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(side_effect=[
        LLMResponse(
            content="",
            tool_calls=[ToolCallRequest(id="1", name="list_dir", arguments={"path": "."})],
            usage={"prompt_tokens": 5, "completion_tokens": 3},
        ),
        LLMResponse(content="done", tool_calls=[], usage={"prompt_tokens": 2, "completion_tokens": 1}),
    ])
    tools = MagicMock()
    tools.get_definitions.return_value = []
    tools.execute = AsyncMock(return_value="ok")

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "x"}],
        tools=tools,
        model="m",
        max_iterations=4,
    ))

    assert result.usage["prompt_tokens"] == 7
    assert result.usage["completion_tokens"] == 4
    assert result.usage["llm_calls"] == 2


@pytest.mark.asyncio
async def test_empty_usage_dict_still_counts_llm_call() -> None:
    provider = MagicMock()
    provider.chat_with_retry = AsyncMock(return_value=LLMResponse(
        content="x", tool_calls=[], usage={},
    ))
    tools = MagicMock()
    tools.get_definitions.return_value = []

    runner = AgentRunner(provider)
    result = await runner.run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hi"}],
        tools=tools,
        model="m",
        max_iterations=2,
    ))

    assert result.usage["prompt_tokens"] == 0
    assert result.usage["completion_tokens"] == 0
    assert result.usage["llm_calls"] == 1
