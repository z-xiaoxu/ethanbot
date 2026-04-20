"""build_status_content formatting and available margin."""

from __future__ import annotations

import time

import pytest

from nanobot.utils.helpers import build_status_content


@pytest.mark.parametrize(
    ("ctx_total", "estimate", "max_tok", "expect_available"),
    [
        (10_000, 5000, 1000, 2976),
        (0, 100, 512, 0),
    ],
)
def test_available_formula(ctx_total: int, estimate: int, max_tok: int, expect_available: int) -> None:
    t0 = time.time()
    text = build_status_content(
        version="0",
        model="m",
        start_time=t0,
        last_usage={"prompt_tokens": 1, "completion_tokens": 1},
        session_usage={"prompt_tokens": 9, "completion_tokens": 2, "llm_calls": 2},
        context_window_tokens=ctx_total,
        session_msg_count=3,
        consolidated_count=1,
        context_tokens_estimate=estimate,
        max_completion_tokens=max_tok,
    )
    assert f"{expect_available} available" in text


def test_context_k_format_uses_thousands_floor() -> None:
    t0 = time.time()
    text = build_status_content(
        version="0",
        model="m",
        start_time=t0,
        last_usage={"prompt_tokens": 0, "completion_tokens": 0},
        session_usage={"prompt_tokens": 0, "completion_tokens": 0, "llm_calls": 0},
        context_window_tokens=8000,
        session_msg_count=1,
        consolidated_count=0,
        context_tokens_estimate=3500,
        max_completion_tokens=0,
    )
    assert "3k/8k" in text


def test_lines_include_last_session_and_messages() -> None:
    t0 = time.time()
    text = build_status_content(
        version="1.2",
        model="test",
        start_time=t0,
        last_usage={"prompt_tokens": 4, "completion_tokens": 1},
        session_usage={"prompt_tokens": 40, "completion_tokens": 2, "llm_calls": 3},
        context_window_tokens=100,
        session_msg_count=5,
        consolidated_count=2,
        context_tokens_estimate=50,
        max_completion_tokens=10,
    )
    assert "Last call: 4 in / 1 out" in text
    assert "Session total: 40 in / 2 out (3 calls)" in text
    assert "Messages: 5 · 2 consolidated" in text
