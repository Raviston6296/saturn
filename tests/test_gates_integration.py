"""
Integration tests for the gates subsystem wired into the agent workflow.

Tests:
  - AutonomousAgent._run_gates() with a mock workspace
  - The fix callback flow (Cursor CLI mode and legacy brain mode)
  - GatePipeline full execution with mocked subprocess
  - TaskResult correctly captures gates results
  - PipelineResult retry history (whole-pipeline restart on failure)
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from gates.config import GateDef, GatesConfig, RulesConfig, RiskConfig, SaturnRepoConfig
from gates.executor import run_gate_pipeline, PipelineResult, GateResult
from gates import GatePipeline, GatePipelineResult
from server.models import TaskResult


# ── Helpers ──────────────────────────────────────────────────────────


def _make_gate(name: str, command: str = "true", retryable: bool = True) -> GateDef:
    return GateDef(name=name, description=f"Gate {name}", command=command, retryable=retryable)


def _make_repo_config(gates: list[GateDef]) -> SaturnRepoConfig:
    return SaturnRepoConfig(
        gates=GatesConfig(gates=gates),
        rules=RulesConfig(),
        risk=RiskConfig(),
        has_config=True,
    )


# ── TaskResult gates fields ───────────────────────────────────────────


class TestTaskResultGatesFields:
    """TaskResult must carry gates_passed and gates_summary."""

    def test_default_gates_passed_is_false(self):
        result = TaskResult(task_id="T-001")
        assert result.gates_passed is False

    def test_default_gates_summary_is_empty(self):
        result = TaskResult(task_id="T-001")
        assert result.gates_summary == ""

    def test_set_gates_passed(self):
        result = TaskResult(task_id="T-001", gates_passed=True)
        assert result.gates_passed is True

    def test_set_gates_summary(self):
        result = TaskResult(task_id="T-001", gates_summary="✅ lint\n✅ tests")
        assert "lint" in result.gates_summary

    def test_gates_and_test_passed_are_independent(self):
        result = TaskResult(task_id="T-001", test_passed=True, gates_passed=False)
        assert result.test_passed is True
        assert result.gates_passed is False


# ── run_gate_pipeline (executor) ──────────────────────────────────────


class TestRunGatePipeline:
    """Test gate executor with the new whole-pipeline retry behavior."""

    def test_all_gates_pass(self, tmp_path):
        gates = [_make_gate("lint"), _make_gate("tests")]
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="OK", stderr="")
            result = run_gate_pipeline(gates, workspace=tmp_path)

        assert result.passed is True
        assert len(result.gate_results) == 2
        assert result.total_retries == 0
        assert result.stopped_at is None

    def test_non_retryable_gate_fails_stops_immediately(self, tmp_path):
        gates = [
            _make_gate("setup", retryable=False),
            _make_gate("compile"),
        ]
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="setup failed")
            result = run_gate_pipeline(gates, workspace=tmp_path)

        assert result.passed is False
        assert result.stopped_at == "setup"
        assert result.total_retries == 0

    def test_retryable_gate_with_no_callback_stops(self, tmp_path):
        gates = [_make_gate("lint")]
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="lint error", stderr="")
            result = run_gate_pipeline(gates, workspace=tmp_path, fix_callback=None)

        assert result.passed is False
        assert result.stopped_at == "lint"

    def test_fix_callback_called_and_all_gates_rerun(self, tmp_path):
        """When a gate fails and is fixed, ALL gates re-run from the beginning."""
        gates = [_make_gate("lint"), _make_gate("tests")]

        # Track subprocess calls to model two full pipeline attempts:
        #   Attempt 1: call 1=lint(pass), call 2=tests(fail)
        #   Attempt 2: call 3=lint(pass), call 4=tests(pass)
        call_count = [0]

        def subprocess_side_effect(*args, **kwargs):
            call_count[0] += 1
            n = call_count[0]
            if n == 1:
                # Attempt 1, gate 1 (lint): passes
                return MagicMock(returncode=0, stdout="lint ok", stderr="")
            elif n == 2:
                # Attempt 1, gate 2 (tests): fails → triggers retry
                return MagicMock(returncode=1, stdout="test failed", stderr="")
            else:
                # Attempt 2: both gates pass
                return MagicMock(returncode=0, stdout="ok", stderr="")

        fix_called = []

        def fix_callback(gate_name, error_output, workspace):
            fix_called.append(gate_name)
            return True

        with patch("subprocess.run", side_effect=subprocess_side_effect):
            result = run_gate_pipeline(gates, workspace=tmp_path, fix_callback=fix_callback)

        assert result.passed is True
        assert fix_called == ["tests"]
        assert result.total_retries == 1
        # Attempt history should show 2 attempts
        assert len(result.attempts) == 2

    def test_max_retries_exhausted(self, tmp_path):
        gates = [_make_gate("flaky")]

        def fix_callback(gate_name, error, workspace):
            return True  # always claims to fix, but subprocess always fails

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="still failing", stderr="")
            result = run_gate_pipeline(
                gates, workspace=tmp_path,
                fix_callback=fix_callback, max_retries=2
            )

        assert result.passed is False
        assert "Max retries" in result.stop_reason
        assert result.total_retries == 2

    def test_fix_callback_returns_false_stops_pipeline(self, tmp_path):
        gates = [_make_gate("compile")]

        def fix_callback(gate_name, error, workspace):
            return False  # agent says it can't fix

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="error", stderr="")
            result = run_gate_pipeline(
                gates, workspace=tmp_path,
                fix_callback=fix_callback, max_retries=3
            )

        assert result.passed is False
        assert "could not fix" in result.stop_reason

    def test_gate_timeout_produces_failed_result(self, tmp_path):
        gates = [_make_gate("slow")]
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 1)):
            result = run_gate_pipeline(gates, workspace=tmp_path, timeout_per_gate=1)

        assert result.passed is False
        assert "timed out" in result.gate_results[0].output

    def test_pipeline_result_summary_shows_retry_history(self, tmp_path):
        """Summary should show attempt history when retries occurred."""
        gates = [_make_gate("tests")]
        call_count = [0]

        def subprocess_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=1, stdout="fail", stderr="")
            return MagicMock(returncode=0, stdout="ok", stderr="")

        def fix_callback(gate_name, error, workspace):
            return True

        with patch("subprocess.run", side_effect=subprocess_side_effect):
            result = run_gate_pipeline(gates, workspace=tmp_path, fix_callback=fix_callback)

        summary = result.summary
        assert "tests" in summary


# ── GatePipeline (orchestrator) ───────────────────────────────────────


class TestGatePipeline:
    """Test GatePipeline end-to-end with mocked subprocess and config loading."""

    def test_skips_when_no_files_changed(self, tmp_path):
        config = _make_repo_config([_make_gate("lint")])
        with patch("gates.GatePipeline.__init__", return_value=None):
            pipeline = GatePipeline.__new__(GatePipeline)
            pipeline.workspace = str(tmp_path)
            pipeline.fix_callback = None
            pipeline.max_retries = 5
            pipeline.timeout_per_gate = 30
            pipeline.config = None

        with patch("gates.load_repo_config", return_value=config), \
             patch("gates.get_changed_files_vs_base", return_value=[]), \
             patch("gates.setup_dpaas_environment", return_value=True):
            result = pipeline.run()

        assert result.skipped is True
        assert result.passed is True

    def test_skips_when_no_gates_configured(self, tmp_path):
        empty_config = _make_repo_config([])

        with patch("gates.GatePipeline.__init__", return_value=None):
            pipeline = GatePipeline.__new__(GatePipeline)
            pipeline.workspace = str(tmp_path)
            pipeline.fix_callback = None
            pipeline.max_retries = 5
            pipeline.timeout_per_gate = 30
            pipeline.config = None

        with patch("gates.load_repo_config", return_value=empty_config), \
             patch("gates.get_changed_files_vs_base", return_value=["foo.py"]), \
             patch("gates.setup_dpaas_environment", return_value=True):
            result = pipeline.run()

        assert result.skipped is True

    def test_pipeline_passes_when_all_gates_pass(self, tmp_path):
        config = _make_repo_config([_make_gate("lint"), _make_gate("tests")])

        with patch("gates.GatePipeline.__init__", return_value=None):
            pipeline = GatePipeline.__new__(GatePipeline)
            pipeline.workspace = str(tmp_path)
            pipeline.fix_callback = None
            pipeline.max_retries = 5
            pipeline.timeout_per_gate = 30
            pipeline.config = None

        with patch("gates.load_repo_config", return_value=config), \
             patch("gates.get_changed_files_vs_base", return_value=["src/foo.py"]), \
             patch("gates.setup_dpaas_environment", return_value=True), \
             patch("gates.check_risk") as mock_risk, \
             patch("gates.run_gate_pipeline") as mock_run:

            mock_risk.return_value = MagicMock(passed=True, violations=[], summary="")
            mock_run.return_value = PipelineResult(passed=True)
            result = pipeline.run()

        assert result.passed is True
        assert not result.skipped

    def test_pipeline_blocked_by_risk_check(self, tmp_path):
        config = _make_repo_config([_make_gate("lint")])

        with patch("gates.GatePipeline.__init__", return_value=None):
            pipeline = GatePipeline.__new__(GatePipeline)
            pipeline.workspace = str(tmp_path)
            pipeline.fix_callback = None
            pipeline.max_retries = 5
            pipeline.timeout_per_gate = 30
            pipeline.config = None

        with patch("gates.load_repo_config", return_value=config), \
             patch("gates.get_changed_files_vs_base", return_value=["src/foo.py"]), \
             patch("gates.setup_dpaas_environment", return_value=True), \
             patch("gates.check_risk") as mock_risk:

            mock_risk.return_value = MagicMock(
                passed=False,
                violations=["Too many files changed: 50 > 20"],
                summary="Risk: too many files",
            )
            result = pipeline.run()

        assert result.passed is False
        assert not result.risk.passed


# ── GatePipelineResult ────────────────────────────────────────────────


class TestGatePipelineResult:
    """Test GatePipelineResult properties."""

    def test_passed_when_skipped(self):
        result = GatePipelineResult(skipped=True, skip_reason="no files changed")
        assert result.passed is True

    def test_passed_requires_both_risk_and_gates(self):
        result = GatePipelineResult()
        result.risk = MagicMock(passed=True)
        result.gates = PipelineResult(passed=False)
        assert result.passed is False

    def test_skipped_summary_shows_reason(self):
        result = GatePipelineResult(skipped=True, skip_reason="No files changed")
        assert "No files changed" in result.summary

    def test_summary_shows_changed_files_count(self):
        result = GatePipelineResult()
        result.changed_files = ["a.py", "b.py"]
        result.risk = MagicMock(passed=True, summary="")
        result.gates = PipelineResult(passed=True)
        assert "2" in result.summary


# ── Agent._run_gates integration ──────────────────────────────────────


class TestAgentRunGatesIntegration:
    """
    Test AutonomousAgent._run_gates() with a mock GatePipeline.

    We mock out GatePipeline to avoid needing a real git workspace or
    running actual commands — focus is on the agent integration logic.
    """

    def _make_agent(self, tmp_path):
        """Create a minimal AutonomousAgent with mocked dependencies."""
        with patch("agent.agent.settings") as mock_settings:
            mock_settings.llm_provider = "cursor"
            mock_settings.max_loop_iterations = 10
            mock_settings.gitlab_project_id = "test-project"

            with patch("agent.agent.CursorCLI"), \
                 patch("agent.agent.ToolExecutor"), \
                 patch("agent.agent.AgentMemory"), \
                 patch("agent.agent.ContextBuilder"):

                from agent.agent import AutonomousAgent
                agent = AutonomousAgent.__new__(AutonomousAgent)
                agent.workspace = str(tmp_path)
                agent.branch_name = "test-branch"
                agent.repo_name = "test-repo"
                agent.repo_manager = None
                agent.use_cursor = True
                agent.cursor = MagicMock()
                agent.brain = None
                agent.executor = MagicMock()
                agent.memory = MagicMock()
                agent.context_builder = MagicMock()
                agent.loop_count = 1
                agent.files_changed = ["src/foo.py"]
                agent.tests_passed = False
                agent.gates_result = None
                agent.pr_url = None
                agent._start_time = 0.0
                agent._last_tool_sig = ""
                agent._repeat_count = 0
                agent._total_nudges = 0
                agent._file_edit_count = {}

                return agent

    def test_run_gates_returns_true_when_gates_pass(self, tmp_path):
        agent = self._make_agent(tmp_path)

        mock_result = MagicMock(spec=GatePipelineResult)
        mock_result.passed = True
        mock_result.summary = "✅ All gates passed"
        mock_result.skipped = False

        with patch("agent.agent.GatePipeline") as MockPipeline:
            MockPipeline.return_value.run.return_value = mock_result
            result = agent._run_gates("Fix the bug")

        assert result is True
        assert agent.gates_result is mock_result

    def test_run_gates_returns_false_when_gates_fail(self, tmp_path):
        agent = self._make_agent(tmp_path)

        mock_result = MagicMock(spec=GatePipelineResult)
        mock_result.passed = False
        mock_result.summary = "❌ lint failed"
        mock_result.skipped = False

        with patch("agent.agent.GatePipeline") as MockPipeline:
            MockPipeline.return_value.run.return_value = mock_result
            result = agent._run_gates("Fix the bug")

        assert result is False

    def test_run_gates_skipped_when_no_files_changed(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent.files_changed = []  # no changes → gates should be skipped

        with patch("agent.agent.GatePipeline") as MockPipeline:
            result = agent._run_gates("Fix the bug")

        # GatePipeline should NOT be instantiated when no files changed
        MockPipeline.assert_not_called()
        assert result is True

    def test_gate_fix_callback_cursor_mode_success(self, tmp_path):
        agent = self._make_agent(tmp_path)

        fix_result = MagicMock()
        fix_result.files_changed = ["src/foo.py"]
        fix_result.success = True
        agent.cursor.run.return_value = fix_result

        result = agent._gate_fix_callback("lint", "lint error output", str(tmp_path))

        assert result is True
        agent.cursor.run.assert_called_once()
        # The changed file should be tracked
        assert "src/foo.py" in agent.files_changed

    def test_gate_fix_callback_cursor_mode_no_changes(self, tmp_path):
        agent = self._make_agent(tmp_path)

        fix_result = MagicMock()
        fix_result.files_changed = []
        fix_result.success = True
        agent.cursor.run.return_value = fix_result

        result = agent._gate_fix_callback("lint", "lint error", str(tmp_path))

        # Cursor reported success but no files changed
        assert result is True  # result.success is True

    def test_gate_fix_callback_cursor_mode_failure(self, tmp_path):
        agent = self._make_agent(tmp_path)

        fix_result = MagicMock()
        fix_result.files_changed = []
        fix_result.success = False
        fix_result.error = "Cursor crashed"
        agent.cursor.run.return_value = fix_result

        result = agent._gate_fix_callback("lint", "lint error", str(tmp_path))

        assert result is False

    def test_gate_fix_callback_no_cursor_no_brain(self, tmp_path):
        agent = self._make_agent(tmp_path)
        agent.use_cursor = False
        agent.use_goose = False
        agent.cursor = None
        agent.goose = None
        agent.brain = None

        result = agent._gate_fix_callback("lint", "lint error", str(tmp_path))

        assert result is False


# ── Worker TaskResult gates fields ────────────────────────────────────


class TestWorkerGatesCapture:
    """
    Verify that the worker correctly captures gates_passed and gates_summary
    from the agent into the TaskResult.
    """

    def test_task_result_captures_gates_passed(self):
        result = TaskResult(task_id="T-123")
        mock_gates = MagicMock()
        mock_gates.passed = True
        mock_gates.summary = "✅ lint\n✅ tests"

        # Simulate what worker does after agent.run()
        result.gates_passed = mock_gates.passed
        result.gates_summary = mock_gates.summary

        assert result.gates_passed is True
        assert "lint" in result.gates_summary

    def test_task_result_captures_gates_failed(self):
        result = TaskResult(task_id="T-124")
        mock_gates = MagicMock()
        mock_gates.passed = False
        mock_gates.summary = "❌ tests failed"

        result.gates_passed = mock_gates.passed
        result.gates_summary = mock_gates.summary

        assert result.gates_passed is False

    def test_task_result_gates_default_true_when_no_gates_result(self):
        """When agent has no gates_result (skipped), worker sets gates_passed=True."""
        result = TaskResult(task_id="T-125")
        agent_gates_result = None

        # Simulate worker logic
        if agent_gates_result:
            result.gates_passed = agent_gates_result.passed
            result.gates_summary = agent_gates_result.summary
        else:
            result.gates_passed = True  # gates skipped → treat as passed

        assert result.gates_passed is True


# ── Goose CLI integration ──────────────────────────────────────────────


class TestGooseCLIIntegration:
    """
    Tests for GooseCLI and agent Goose-mode integration.
    All subprocess calls are mocked — no real goose binary needed.
    """

    def test_goose_result_default_success(self):
        from agent.goose_cli import GooseResult
        result = GooseResult()
        assert result.success is True
        assert result.exit_code == 0
        assert result.output == ""

    def test_goose_result_failed(self):
        from agent.goose_cli import GooseResult
        result = GooseResult(
            output="error",
            exit_code=1,
            success=False,
            error="goose exited with code 1",
        )
        assert result.success is False
        assert result.exit_code == 1

    def test_goose_result_files_changed(self):
        from agent.goose_cli import GooseResult
        result = GooseResult(
            output="done",
            files_changed=["source/com/zoho/dpaas/transformer/ZDFilter.scala"],
        )
        assert len(result.files_changed) == 1

    def test_goose_result_summary_strips_ansi(self):
        from agent.goose_cli import GooseResult
        result = GooseResult(output="\x1b[32mgreen text\x1b[0m")
        assert "\x1b" not in result.summary
        assert "green text" in result.summary

    def test_goose_cli_build_command(self):
        """GooseCLI._build_command produces expected command."""
        from agent.goose_cli import GooseCLI
        from unittest.mock import patch

        with patch.object(GooseCLI, "_verify_cli"):
            cli = GooseCLI.__new__(GooseCLI)
            cli.goose_path = "goose"
            cli.timeout = 600

        cmd = cli._build_command("Fix the bug")
        assert cmd[0] == "goose"
        assert "run" in cmd
        assert "--text" in cmd
        assert "Fix the bug" in cmd
        assert "--with-builtin" in cmd
        assert "developer" in cmd

    def test_agent_goose_mode_flag(self, tmp_path):
        """Agent sets use_goose=True when LLM_PROVIDER=goose."""
        from agent.agent import AutonomousAgent
        from unittest.mock import patch

        with patch("agent.agent.settings") as mock_settings, \
             patch("agent.agent.ToolExecutor"), \
             patch("agent.agent.AgentMemory"), \
             patch("agent.agent.ContextBuilder"), \
             patch("agent.goose_cli.GooseCLI._verify_cli"):

            mock_settings.llm_provider = "goose"
            mock_settings.goose_cli_path = "goose"
            mock_settings.goose_timeout_seconds = 600
            mock_settings.goose_provider = ""
            mock_settings.goose_model = ""
            mock_settings.max_loop_iterations = 10
            mock_settings.gitlab_project_id = "test"

            agent = AutonomousAgent(workspace=str(tmp_path))

            assert agent.use_goose is True
            assert agent.use_cursor is False
            assert agent.goose is not None
            assert agent.cursor is None
            assert agent.brain is None

    def test_gate_fix_callback_goose_mode_success(self, tmp_path):
        """Agent in Goose mode calls goose.fix() when gate fails."""
        from agent.agent import AutonomousAgent
        from agent.goose_agent import GooseAgent, GooseAgentResult
        from unittest.mock import MagicMock

        agent = AutonomousAgent.__new__(AutonomousAgent)
        agent.workspace = str(tmp_path)
        agent.use_cursor = False
        agent.use_goose = True
        agent.cursor = None
        agent.brain = None
        agent.files_changed = ["source/ZDFilter.scala"]
        agent.goose = MagicMock(spec=GooseAgent)

        fix_result = GooseAgentResult(
            files_changed=["source/ZDFilter.scala"],
            success=True,
        )
        agent.goose.fix.return_value = fix_result

        result = agent._gate_fix_callback("compile", "scalac error output", str(tmp_path))

        assert result is True
        agent.goose.fix.assert_called_once()

    def test_gate_fix_callback_goose_mode_failure(self, tmp_path):
        """Goose fails to fix the gate error."""
        from agent.agent import AutonomousAgent
        from agent.goose_agent import GooseAgent, GooseAgentResult
        from unittest.mock import MagicMock

        agent = AutonomousAgent.__new__(AutonomousAgent)
        agent.workspace = str(tmp_path)
        agent.use_cursor = False
        agent.use_goose = True
        agent.cursor = None
        agent.brain = None
        agent.files_changed = []
        agent.goose = MagicMock(spec=GooseAgent)

        agent.goose.fix.return_value = GooseAgentResult(
            files_changed=[], success=False, error="goose timed out after 600s"
        )
        result = agent._gate_fix_callback("compile", "scalac error", str(tmp_path))

        assert result is False
