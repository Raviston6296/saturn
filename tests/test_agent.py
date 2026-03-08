"""
Tests for the agent brain and core loop.
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
    """Test the memory/log system."""

    def test_log_and_retrieve(self, tmp_path):
        memory = AgentMemory(str(tmp_path))
        memory.log_action("read_file", "OK: Read main.py")
        memory.log_action("edit_file", "OK: Edited main.py")

        recent = memory.get_recent_actions(5)
        assert len(recent) == 2
        assert recent[0]["action"] == "read_file"

    def test_history_summary(self, tmp_path):
        memory = AgentMemory(str(tmp_path))
        memory.log_action("run_command", "EXIT CODE: 0")
        summary = memory.get_history_summary()
        assert "run_command" in summary

    def test_empty_history(self, tmp_path):
        memory = AgentMemory(str(tmp_path))
        summary = memory.get_history_summary()
        assert "no previous" in summary.lower()


class TestContextBuilder:
    """Test workspace context snapshot building."""

    def test_build_snapshot(self, tmp_path):
        # Create a simple file
        (tmp_path / "hello.py").write_text("print('hello')")
        builder = ContextBuilder(str(tmp_path))
        snapshot = builder.build_snapshot()

        assert "FILE TREE" in snapshot
        assert "hello.py" in snapshot


class TestPrompts:
    """Test that prompts are well-formed."""

    def test_system_prompt_not_empty(self):
        assert len(SYSTEM_PROMPT) > 100

    def test_system_prompt_has_key_sections(self):
        assert "EXECUTION LOOP" in SYSTEM_PROMPT
        assert "TOOL RULES" in SYSTEM_PROMPT
        assert "HARD RULES" in SYSTEM_PROMPT

    def test_hard_problem_addon(self):
        assert "extended thinking" in HARD_PROBLEM_ADDON.lower()
        assert "hypothesis" in HARD_PROBLEM_ADDON.lower()

