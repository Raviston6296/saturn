"""
Tests for the agent brain, memory, context, and prompts.
"""

import pytest

from agent.prompts import SYSTEM_PROMPT, HARD_PROBLEM_ADDON
from agent.memory import AgentMemory
from agent.context import ContextBuilder
from agent.brain import AgentBrain


class TestAgentBrainClassification:
    """Test task difficulty classification."""

    def setup_method(self):
        self.brain = AgentBrain(tools=[])

    def test_hard_problem_debug(self):
        assert self.brain.classify_difficulty("Debug the authentication crash") is True

    def test_hard_problem_refactor(self):
        assert self.brain.classify_difficulty("Refactor the entire auth module") is True

    def test_hard_problem_race_condition(self):
        assert self.brain.classify_difficulty("Fix the race condition in worker") is True

    def test_hard_problem_performance(self):
        assert self.brain.classify_difficulty("Improve performance of search") is True

    def test_simple_task(self):
        assert self.brain.classify_difficulty("Add a new endpoint for /users") is False

    def test_simple_task_typo(self):
        assert self.brain.classify_difficulty("Fix typo in README") is False


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

