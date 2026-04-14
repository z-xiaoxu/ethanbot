"""Skill proposal JSON examples aligned with skill-proposal.v1.schema.json."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field


class SkillProposalV1(BaseModel):
    """Subset mirror of contracts/skill-proposal.v1.schema.json for example validation."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^prop-[0-9]{8}-[a-zA-Z0-9_-]+$")
    type: Literal["create", "patch"]
    status: Literal["pending", "accepted", "rejected"]
    skill_name: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    proposed_content: str = Field(min_length=1)
    source_threads: list[str] = Field(min_length=1)
    created_at: str = Field(min_length=1)
    target_skill: str | None = None
    diff_description: str | None = None
    original_content: str | None = None


def test_schema_file_exists() -> None:
    root = Path(__file__).resolve().parents[2]
    p = root / "specs/20260413-skill-discover-patch/contracts/skill-proposal.v1.schema.json"
    assert p.is_file()


def test_minimal_create_example_validates() -> None:
    doc = {
        "id": "prop-20260413-a1b2c3d4",
        "type": "create",
        "status": "pending",
        "skill_name": "deploy-ppe",
        "reason": "Repeated PPE deploy steps across threads.",
        "proposed_content": "---\nname: deploy-ppe\ndescription: x\n---\n# Skill\n",
        "source_threads": ["telegram:123:2026-04-10"],
        "created_at": "2026-04-13T10:30:00Z",
        "target_skill": None,
        "diff_description": None,
        "original_content": None,
    }
    SkillProposalV1.model_validate(doc)


def test_minimal_patch_example_validates() -> None:
    doc = {
        "id": "prop-20260413-abcdef01",
        "type": "patch",
        "status": "pending",
        "skill_name": "demo",
        "reason": "Recovery required an undocumented fallback.",
        "proposed_content": "---\nname: demo\n---\n# Fixed\n",
        "source_threads": ["cli:direct"],
        "created_at": "2026-04-13T12:00:00Z",
        "target_skill": "demo",
        "diff_description": "Add fallback when the API returns 429.",
        "original_content": "---\nname: demo\n---\n# Old\n",
    }
    SkillProposalV1.model_validate(doc)


@pytest.mark.parametrize("bad_id", ["prop-2026-abc", "x", "prop-20260413"])
def test_id_pattern_rejects_invalid(bad_id: str) -> None:
    doc = {
        "id": bad_id,
        "type": "create",
        "status": "pending",
        "skill_name": "s",
        "reason": "r",
        "proposed_content": "c",
        "source_threads": ["t"],
        "created_at": "2026-04-13T10:30:00Z",
    }
    with pytest.raises(Exception):
        SkillProposalV1.model_validate(doc)
