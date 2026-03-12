"""
Gate executor — runs deterministic gates with self-healing retry loop.

Execution flow:
  1. Run all gates sequentially
  2. If ANY gate fails:
     - If retryable + fix_callback → send error to agent → agent fixes
     - Re-run ALL gates from the beginning (fix might affect earlier gates)
     - Repeat until all pass or max_retries exhausted
  3. If not retryable → stop permanently

The key insight: we retry the ENTIRE pipeline, not individual gates.
A fix for a compile error might introduce a new formatting issue,
or a test fix might break compilation. Always re-validate from scratch.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from gates.config import GateDef


MAX_GATE_RETRIES = 5


@dataclass
class GateResult:
    """Result of a single gate execution."""
    gate_name: str
    passed: bool
    exit_code: int = 0
    output: str = ""
    attempts: int = 1


@dataclass
class PipelineAttempt:
    """Record of one full pipeline attempt."""
    attempt_number: int
    gate_results: list[GateResult] = field(default_factory=list)
    failed_gate: str | None = None
    fix_applied: bool = False

    @property
    def passed(self) -> bool:
        return all(gr.passed for gr in self.gate_results)


@dataclass
class PipelineResult:
    """Result of running the full gate pipeline with retry history."""
    passed: bool = True
    gate_results: list[GateResult] = field(default_factory=list)
    total_retries: int = 0
    stopped_at: str | None = None
    stop_reason: str = ""
    attempts: list[PipelineAttempt] = field(default_factory=list)

    @property
    def summary(self) -> str:
        lines = []

        if len(self.attempts) > 1:
            lines.append(f"  🔄 {len(self.attempts)} attempts ({self.total_retries} retries)")
            lines.append("")
            for attempt in self.attempts[:-1]:
                if attempt.failed_gate:
                    fix_label = "🔧 agent fixed" if attempt.fix_applied else "⚠️ no fix"
                    lines.append(
                        f"  Attempt {attempt.attempt_number}: "
                        f"❌ failed at [{attempt.failed_gate}] → {fix_label}"
                    )
            lines.append("")
            lines.append(f"  Final attempt ({self.attempts[-1].attempt_number}):")

        for gr in self.gate_results:
            icon = "✅" if gr.passed else "❌"
            lines.append(f"  {icon} {gr.gate_name}")

        if self.stopped_at:
            lines.append(f"  🛑 Pipeline stopped at: {self.stopped_at}")
            if self.stop_reason:
                lines.append(f"     Reason: {self.stop_reason}")

        return "\n".join(lines)


# Type alias for the fix callback:
#   (gate_name, error_output, workspace) → bool (whether the fix was applied)
FixCallback = Callable[[str, str, str], bool]


def run_gate_pipeline(
    gates: list[GateDef],
    workspace: str | Path,
    fix_callback: FixCallback | None = None,
    max_retries: int = MAX_GATE_RETRIES,
    timeout_per_gate: int = 120,
) -> PipelineResult:
    """
    Execute gates with a self-healing retry loop.

    Algorithm:
      1. Run all gates sequentially
      2. If a gate fails:
         a. Not retryable → stop permanently (e.g., setup/bootstrap)
         b. Retryable + no fix_callback → stop (can't self-heal)
         c. Retryable + fix_callback:
            - Send error to agent
            - Agent applies fix
            - Re-run ALL gates from the beginning
            - Repeat until pass or max_retries
      3. Return combined result with full retry history

    Why re-run ALL gates (not just the failed one):
      - Fix for compile error might introduce lint issues
      - Fix for test failure might break compilation
      - Fix for one test might break another test
      - Always re-validate the entire pipeline from scratch
    """
    workspace = str(Path(workspace).resolve())
    pipeline = PipelineResult()
    attempt_number = 0

    while attempt_number <= max_retries:
        attempt_number += 1
        attempt = PipelineAttempt(attempt_number=attempt_number)

        print(f"\n  {'─'*35}")
        if attempt_number > 1:
            print(f"  🔄 Attempt {attempt_number}/{max_retries + 1}")
        else:
            print(f"  ▶️  Running gates...")
        print(f"  {'─'*35}")

        failed_gate: GateDef | None = None
        failed_result: GateResult | None = None

        for gate in gates:
            if not gate.command:
                continue

            print(f"  🚧 [{gate.name}]: {gate.description or gate.command}")

            gate_result = _run_single_gate(gate, workspace, timeout_per_gate)
            attempt.gate_results.append(gate_result)

            if gate_result.passed:
                print(f"  ✅ [{gate.name}] passed")
            else:
                print(f"  ❌ [{gate.name}] FAILED (exit code: {gate_result.exit_code})")
                error_preview = gate_result.output[-500:] if gate_result.output else "(no output)"
                for line in error_preview.strip().splitlines()[-10:]:
                    print(f"     │ {line}")

                failed_gate = gate
                failed_result = gate_result
                attempt.failed_gate = gate.name
                break

        pipeline.attempts.append(attempt)

        # All gates passed
        if not failed_gate:
            pipeline.passed = True
            pipeline.gate_results = attempt.gate_results
            pipeline.total_retries = attempt_number - 1
            if attempt_number > 1:
                print(f"\n  ✅ All gates passed (after {attempt_number - 1} retries)")
            else:
                print(f"\n  ✅ All gates passed")
            return pipeline

        # Gate failed — determine action

        # Not retryable → stop permanently
        if not failed_gate.retryable:
            pipeline.passed = False
            pipeline.gate_results = attempt.gate_results
            pipeline.stopped_at = failed_gate.name
            pipeline.stop_reason = f"Gate [{failed_gate.name}] is not retryable"
            pipeline.total_retries = attempt_number - 1
            print(f"\n  🛑 [{failed_gate.name}] is not retryable — pipeline stopped")
            return pipeline

        # No fix callback → stop (can't self-heal)
        if not fix_callback:
            pipeline.passed = False
            pipeline.gate_results = attempt.gate_results
            pipeline.stopped_at = failed_gate.name
            pipeline.stop_reason = f"Gate [{failed_gate.name}] failed — no fix callback"
            pipeline.total_retries = attempt_number - 1
            print(f"\n  🛑 [{failed_gate.name}] failed — no fix callback — pipeline stopped")
            return pipeline

        # Max retries exhausted
        if attempt_number > max_retries:
            pipeline.passed = False
            pipeline.gate_results = attempt.gate_results
            pipeline.stopped_at = failed_gate.name
            pipeline.stop_reason = f"Max retries ({max_retries}) exhausted"
            pipeline.total_retries = max_retries
            print(f"\n  🛑 Max retries ({max_retries}) exhausted — pipeline stopped")
            return pipeline

        # Ask agent to fix, then re-run ALL gates from the beginning
        # retries_remaining = max_retries - (attempt_number - 1)
        #   e.g. max_retries=5, attempt 1 failed → 5 retries remaining
        print(f"\n  🔧 Asking agent to fix [{failed_gate.name}]...")
        print(f"     Retries remaining: {max_retries - attempt_number + 1}")

        fixed = fix_callback(
            failed_gate.name,
            failed_result.output,
            workspace,
        )
        attempt.fix_applied = fixed
        pipeline.total_retries += 1

        if not fixed:
            pipeline.passed = False
            pipeline.gate_results = attempt.gate_results
            pipeline.stopped_at = failed_gate.name
            pipeline.stop_reason = f"Agent could not fix [{failed_gate.name}]"
            print(f"  ⚠️  Agent could not produce a fix — pipeline stopped")
            return pipeline

        print(f"  🔧 Fix applied — re-running ALL gates from the beginning...")
        # Loop continues → all gates re-run from scratch

    # Safety fallback (should not reach here)
    pipeline.passed = False
    pipeline.stop_reason = "Unexpected exit from retry loop"
    return pipeline


def _run_single_gate(gate: GateDef, workspace: str, timeout: int) -> GateResult:
    """Execute a single gate command and capture the result."""
    try:
        result = subprocess.run(
            gate.command,
            shell=True,
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout + "\n" + result.stderr).strip()

        return GateResult(
            gate_name=gate.name,
            passed=(result.returncode == 0),
            exit_code=result.returncode,
            output=output,
        )
    except subprocess.TimeoutExpired:
        return GateResult(
            gate_name=gate.name,
            passed=False,
            exit_code=-1,
            output=f"Gate [{gate.name}] timed out after {timeout}s",
        )
    except Exception as e:
        return GateResult(
            gate_name=gate.name,
            passed=False,
            exit_code=-1,
            output=f"Gate [{gate.name}] error: {e}",
        )
