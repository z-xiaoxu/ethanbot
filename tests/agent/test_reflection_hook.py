"""Reflection checkpoint hook (AgentLoop run hook)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nanobot.agent.hook import AgentHookContext
from nanobot.agent.loop import _AgentLoopRunHook


@pytest.mark.asyncio
async def test_reflection_checkpoint_only_on_interval_after_first_iteration() -> None:
    loop = MagicMock()
    hook = _AgentLoopRunHook(
        loop,
        on_progress=None,
        on_stream=None,
        on_stream_end=None,
        channel="cli",
        chat_id="direct",
        message_id=None,
    )
    messages: list[dict] = [{"role": "user", "content": "start"}]

    await hook.before_iteration(AgentHookContext(iteration=14, messages=messages))
    assert len(messages) == 1

    await hook.before_iteration(AgentHookContext(iteration=15, messages=messages))
    assert len(messages) == 2
    assert messages[-1]["role"] == "user"
    assert "[Reflection checkpoint — not from user]" in messages[-1]["content"]

    await hook.before_iteration(AgentHookContext(iteration=16, messages=messages))
    assert len(messages) == 2

    await hook.before_iteration(AgentHookContext(iteration=30, messages=messages))
    assert len(messages) == 3
    assert "[Reflection checkpoint — not from user]" in messages[-1]["content"]
