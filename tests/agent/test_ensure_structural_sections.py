"""Tests for _ensure_structural_sections in memory consolidation."""

from nanobot.agent.memory import (
    _DEFAULT_BO_SECTION,
    _DEFAULT_PROFILE_META,
    _DEFAULT_TOPIC_META,
    _ensure_structural_sections,
)

_TEMPLATE_BO = (
    "## Behavioral Observations\n\n"
    "> Instructions...\n\n"
    "### Pending\n\n"
    "- [USER][thread:abc] likes dark mode\n\n"
    "### Synthesized\n\n"
    "*auto*\n\n"
    "<!-- nanobot-profile-meta: last_synth_iso=2026-04-01T00:00:00Z pending_count=1 -->\n"
)

_TEMPLATE_TOPIC_META = "<!-- nanobot-topic-meta: last_synth_iso=2026-03-15T12:00:00Z -->"

_FULL_CURRENT = (
    "## About\n\nsome facts\n\n"
    f"{_TEMPLATE_TOPIC_META}\n\n"
    f"{_TEMPLATE_BO}"
)


def test_all_sections_preserved_unchanged() -> None:
    """When LLM output already contains all structural sections, nothing changes."""
    update = _FULL_CURRENT
    result = _ensure_structural_sections(update, _FULL_CURRENT)
    assert "## Behavioral Observations" in result
    assert "nanobot-topic-meta" in result
    assert "nanobot-profile-meta" in result
    assert "2026-03-15T12:00:00Z" in result
    assert "2026-04-01T00:00:00Z" in result


def test_bo_section_dropped_restored_from_current() -> None:
    """When LLM drops Behavioral Observations, restore from current memory."""
    update = "## About\n\nnew facts only"
    result = _ensure_structural_sections(update, _FULL_CURRENT)
    assert "## Behavioral Observations" in result
    assert "### Pending" in result
    assert "### Synthesized" in result
    assert "[USER][thread:abc] likes dark mode" in result
    assert "nanobot-profile-meta" in result


def test_bo_section_dropped_defaults_when_current_empty() -> None:
    """When both current and update lack BO, fall back to built-in defaults."""
    update = "## About\n\nnew facts"
    result = _ensure_structural_sections(update, "")
    assert "## Behavioral Observations" in result
    assert "### Pending" in result
    assert "### Synthesized" in result
    assert _DEFAULT_PROFILE_META in result


def test_bo_body_mangled_replaced_from_current() -> None:
    """When LLM keeps the BO heading but mangles the body, replace with original."""
    mangled = "## About\n\nfacts\n\n## Behavioral Observations\n\ngarbage text only"
    result = _ensure_structural_sections(mangled, _FULL_CURRENT)
    assert "### Pending" in result
    assert "[USER][thread:abc] likes dark mode" in result
    assert "garbage text only" not in result


def test_topic_meta_dropped_restored_from_current() -> None:
    """When LLM drops topic meta comment, restore the original value."""
    update = "## About\n\nfacts\n\n" + _TEMPLATE_BO
    result = _ensure_structural_sections(update, _FULL_CURRENT)
    assert "nanobot-topic-meta" in result
    assert "2026-03-15T12:00:00Z" in result


def test_topic_meta_dropped_defaults_when_current_empty() -> None:
    """When current has no topic meta either, inject the built-in default."""
    update = "## About\n\nfacts"
    result = _ensure_structural_sections(update, "")
    assert _DEFAULT_TOPIC_META in result


def test_topic_meta_inserted_before_bo() -> None:
    """Topic meta should appear before Behavioral Observations."""
    update = "## About\n\nfacts\n\n" + _TEMPLATE_BO
    result = _ensure_structural_sections(update, "")
    topic_pos = result.index("nanobot-topic-meta")
    bo_pos = result.index("## Behavioral Observations")
    assert topic_pos < bo_pos


def test_profile_meta_dropped_restored_from_current() -> None:
    """When LLM drops profile meta, restore the original value."""
    update = (
        "## About\n\nfacts\n\n"
        f"{_TEMPLATE_TOPIC_META}\n\n"
        "## Behavioral Observations\n\n### Pending\n\n### Synthesized\n"
    )
    result = _ensure_structural_sections(update, _FULL_CURRENT)
    assert "nanobot-profile-meta" in result
    assert "2026-04-01T00:00:00Z" in result


def test_profile_meta_dropped_defaults_when_current_empty() -> None:
    """When current has no profile meta either, inject the built-in default."""
    update = "## About\n\nfacts"
    result = _ensure_structural_sections(update, "")
    assert _DEFAULT_PROFILE_META in result


def test_content_above_bo_is_not_mutated() -> None:
    """User content above Behavioral Observations must survive intact."""
    update = "## User\n\nidentity info\n\n## Projects\n\nproject data"
    result = _ensure_structural_sections(update, _FULL_CURRENT)
    assert "## User\n\nidentity info" in result
    assert "## Projects\n\nproject data" in result
