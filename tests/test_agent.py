"""
Tests for the agent brain, memory, context, and prompts.
"""

import pytest
from unittest.mock import patch

from agent.prompts import SYSTEM_PROMPT, HARD_PROBLEM_ADDON
from agent.memory import AgentMemory
from agent.context import ContextBuilder
from agent.cursor_cli import CursorResult, _strip_ansi


class TestCursorResult:
    """Test CursorResult dataclass."""

    def test_default_success(self):
        result = CursorResult()
        assert result.success is True
        assert result.exit_code == 0
        assert result.output == ""

    def test_failed_result(self):
        result = CursorResult(
            output="error output",
            exit_code=1,
            success=False,
            error="Cursor CLI exited with code 1",
        )
        assert result.success is False
        assert result.exit_code == 1

    def test_files_changed_tracking(self):
        result = CursorResult(
            output="done",
            files_changed=["src/main.py", "README.md"],
        )
        assert len(result.files_changed) == 2
        assert "src/main.py" in result.files_changed

    def test_summary_strips_ansi(self):
        result = CursorResult(output="\x1b[32mgreen text\x1b[0m")
        assert "\x1b" not in result.summary
        assert "green text" in result.summary


class TestStripAnsi:
    """Test ANSI stripping utility."""

    def test_strips_color_codes(self):
        assert _strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_plain_text_unchanged(self):
        assert _strip_ansi("hello world") == "hello world"

    def test_empty_string(self):
        assert _strip_ansi("") == ""


class TestDifficultyClassification:
    """Test task difficulty classification (extracted logic)."""

    @staticmethod
    def _classify(task: str) -> bool:
        """Replicate classify_difficulty logic without needing AgentBrain."""
        hard_signals = [
            "architecture", "design", "refactor", "migrate",
            "debug", "mysterious", "intermittent", "race condition",
            "performance", "security", "scale", "why", "figure out",
            "broken", "fails randomly", "production issue", "crash",
            "memory leak", "deadlock", "complex",
        ]
        task_lower = task.lower()
        return any(signal in task_lower for signal in hard_signals)

    def test_hard_problem_debug(self):
        assert self._classify("Debug the authentication crash") is True

    def test_hard_problem_refactor(self):
        assert self._classify("Refactor the entire auth module") is True

    def test_hard_problem_race_condition(self):
        assert self._classify("Fix the race condition in worker") is True

    def test_hard_problem_performance(self):
        assert self._classify("Improve performance of search") is True

    def test_simple_task(self):
        assert self._classify("Add a new endpoint for /users") is False

    def test_simple_task_typo(self):
        assert self._classify("Fix typo in README") is False


class TestAgentMemory:
    """Test the two-tier memory system."""

    def test_task_log(self, tmp_path):
        memory = AgentMemory(str(tmp_path))
        memory.log_action("read_file", "OK: Read main.py")
        memory.log_action("edit_file", "OK: Edited main.py")

        recent = memory.get_recent_actions(5)
        assert len(recent) == 2
        assert recent[0]["action"] == "read_file"

    def test_history_summary_empty(self, tmp_path):
        memory = AgentMemory(str(tmp_path))
        summary = memory.get_history_summary()
        assert "no actions" in summary.lower() or "no previous" in summary.lower()

    def test_history_summary_with_actions(self, tmp_path):
        memory = AgentMemory(str(tmp_path))
        memory.log_action("run_command", "EXIT CODE: 0")
        summary = memory.get_history_summary()
        assert "run_command" in summary

    def test_repo_memory_persistence(self, tmp_path):
        """Repo memory persists across AgentMemory instances."""
        repo_dir = tmp_path / "bare_repo"
        repo_dir.mkdir()

        # First task saves to repo memory
        mem1 = AgentMemory(str(tmp_path), repo_memory_dir=str(repo_dir))
        mem1.save_task_summary("task-1", "Fix login bug", "Fixed auth timeout", "https://pr/1")

        # Second task reads from same repo memory
        mem2 = AgentMemory(str(tmp_path), repo_memory_dir=str(repo_dir))
        past = mem2.get_past_tasks()
        assert len(past) == 1
        assert past[0]["task_id"] == "task-1"

    def test_knowledge_storage(self, tmp_path):
        repo_dir = tmp_path / "bare_repo"
        repo_dir.mkdir()

        memory = AgentMemory(str(tmp_path), repo_memory_dir=str(repo_dir))
        memory.add_knowledge("test_framework", "pytest with conftest.py")

        # Reload and verify
        mem2 = AgentMemory(str(tmp_path), repo_memory_dir=str(repo_dir))
        knowledge = mem2.repo_memory.get("knowledge", {})
        assert "test_framework" in knowledge


class TestContextBuilder:
    """Test workspace context snapshot building."""

    def test_build_snapshot_without_repo_manager(self, tmp_path):
        (tmp_path / "hello.py").write_text("print('hello')")
        builder = ContextBuilder(str(tmp_path))
        snapshot = builder.build_snapshot()
        assert "WORKTREE STATE" in snapshot
        assert "hello.py" in snapshot

    def test_build_snapshot_has_no_repo_context_without_manager(self, tmp_path):
        builder = ContextBuilder(str(tmp_path))
        snapshot = builder.build_snapshot()
        assert "REPO KNOWLEDGE" not in snapshot


class TestPrompts:
    """Test that prompts are well-formed."""

    def test_system_prompt_not_empty(self):
        assert len(SYSTEM_PROMPT) > 100

    def test_system_prompt_has_key_sections(self):
        assert "EXECUTION LOOP" in SYSTEM_PROMPT
        assert "TOOL RULES" in SYSTEM_PROMPT
        assert "HARD RULES" in SYSTEM_PROMPT

    def test_system_prompt_is_saturn(self):
        assert "Saturn" in SYSTEM_PROMPT

    def test_hard_problem_addon(self):
        assert "extended thinking" in HARD_PROBLEM_ADDON.lower()
        assert "hypothesis" in HARD_PROBLEM_ADDON.lower()

