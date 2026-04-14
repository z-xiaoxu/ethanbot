"""Unit tests for _sync_heartbeat_system_tasks in nanobot/utils/helpers.py."""

from pathlib import Path

import pytest

from nanobot.utils.helpers import _sync_heartbeat_system_tasks, sync_workspace_templates


TEMPLATE_HEARTBEAT = """\
# Heartbeat Tasks

This file is checked every 30 minutes by your nanobot agent.

## System Tasks

<!-- Built-in background tasks. Results are silently processed — no user notification. -->

### Skill Discovery

Steps for skill discovery go here.

### Profile Synthesis (USER.md / SOUL.md dynamic sections)

Steps for profile synthesis go here.

## Active Tasks

<!-- Add your periodic tasks below this line. You will be notified of results. -->

## Completed

<!-- Move completed tasks here or delete them -->
"""


class TestSyncHeartbeatSystemTasks:
    """Direct tests for _sync_heartbeat_system_tasks."""

    def test_outdated_system_tasks_are_replaced(self, tmp_path: Path):
        user_file = tmp_path / "HEARTBEAT.md"
        user_file.write_text(
            "# Heartbeat Tasks\n\n"
            "## System Tasks\n\n"
            "<!-- old comment -->\n\n"
            "### Old Task\n\nOld content.\n\n"
            "## Active Tasks\n\n"
            "My custom task here.\n\n"
            "## Completed\n\nDone stuff.\n",
            encoding="utf-8",
        )

        changed = _sync_heartbeat_system_tasks(user_file, TEMPLATE_HEARTBEAT)

        assert changed is True
        result = user_file.read_text(encoding="utf-8")
        assert "### Skill Discovery" in result
        assert "### Profile Synthesis" in result
        assert "### Old Task" not in result
        assert "My custom task here." in result
        assert "Done stuff." in result

    def test_already_matching_is_noop(self, tmp_path: Path):
        user_file = tmp_path / "HEARTBEAT.md"
        user_file.write_text(TEMPLATE_HEARTBEAT, encoding="utf-8")

        changed = _sync_heartbeat_system_tasks(user_file, TEMPLATE_HEARTBEAT)

        assert changed is False
        assert user_file.read_text(encoding="utf-8") == TEMPLATE_HEARTBEAT

    def test_no_system_tasks_section_returns_false(self, tmp_path: Path):
        user_file = tmp_path / "HEARTBEAT.md"
        user_file.write_text(
            "# Heartbeat Tasks\n\n## Active Tasks\n\nSome task.\n",
            encoding="utf-8",
        )

        changed = _sync_heartbeat_system_tasks(user_file, TEMPLATE_HEARTBEAT)

        assert changed is False

    def test_preserves_content_before_system_tasks(self, tmp_path: Path):
        header = "# Heartbeat Tasks\n\nCustom intro paragraph.\n\n"
        user_file = tmp_path / "HEARTBEAT.md"
        user_file.write_text(
            header
            + "## System Tasks\n\n### Stale\n\nOld.\n\n"
            + "## Active Tasks\n\nKeep this.\n",
            encoding="utf-8",
        )

        _sync_heartbeat_system_tasks(user_file, TEMPLATE_HEARTBEAT)

        result = user_file.read_text(encoding="utf-8")
        assert result.startswith(header)
        assert "### Skill Discovery" in result
        assert "Keep this." in result

    def test_template_without_system_tasks_returns_false(self, tmp_path: Path):
        user_file = tmp_path / "HEARTBEAT.md"
        user_file.write_text(TEMPLATE_HEARTBEAT, encoding="utf-8")
        bad_template = "# No sections\n\nJust text.\n"

        changed = _sync_heartbeat_system_tasks(user_file, bad_template)

        assert changed is False


class TestSyncWorkspaceTemplatesHeartbeatIntegration:
    """Integration: sync_workspace_templates calls _sync_heartbeat_system_tasks."""

    def test_existing_heartbeat_gets_system_tasks_updated(self, tmp_path: Path):
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        heartbeat = workspace / "HEARTBEAT.md"
        heartbeat.write_text(
            "# Heartbeat Tasks\n\n"
            "## System Tasks\n\n"
            "<!-- placeholder -->\n\n"
            "## Active Tasks\n\n"
            "## Completed\n",
            encoding="utf-8",
        )

        sync_workspace_templates(workspace, silent=True)

        result = heartbeat.read_text(encoding="utf-8")
        assert "### Skill Discovery" in result
        assert "## Active Tasks" in result
