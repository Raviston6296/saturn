"""
Gate executor — runs deterministic gates sequentially inside a worktree.

Execution rules (from spec):
  1. Run each gate command inside the repository worktree
  2. Capture stdout and stderr
  3. If the gate fails:
     - retryable=true  → agent attempts to fix, then re-runs the gate
     - retryable=false → pipeline stops immediately
  4. Max retries enforced globally (MAX_GATE_RETRIES)
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
class PipelineResult:
    """Result of running the full gate pipeline."""
    passed: bool = True
    gate_results: list[GateResult] = field(default_factory=list)
    total_retries: int = 0
    stopped_at: str | None = None

    @property
    def summary(self) -> str:
        lines = []
        for gr in self.gate_results:
            icon = "✅" if gr.passed else "❌"
            retry_info = f" ({gr.attempts} attempts)" if gr.attempts > 1 else ""
            lines.append(f"  {icon} {gr.gate_name}{retry_info}")
        if self.stopped_at:
            lines.append(f"  🛑 Pipeline stopped at: {self.stopped_at}")
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
    Execute gates sequentially. On failure:
      - If retryable and fix_callback is provided, ask the agent to fix,
        then retry the gate (up to max_retries total across all gates).
      - If not retryable, stop the pipeline.
    """
    workspace = str(Path(workspace).resolve())
    pipeline = PipelineResult()
    retries_remaining = max_retries

    for gate in gates:
        if not gate.command:
            continue

        print(f"  🚧 Gate [{gate.name}]: {gate.description or gate.command}")

        gate_result = _run_single_gate(gate, workspace, timeout_per_gate)

        while not gate_result.passed and gate.retryable and retries_remaining > 0 and fix_callback:
            retries_remaining -= 1
            pipeline.total_retries += 1
            gate_result.attempts += 1

            print(f"  🔄 Gate [{gate.name}] failed — asking agent to fix "
                  f"(retry {gate_result.attempts}, {retries_remaining} retries left)")

            fixed = fix_callback(gate.name, gate_result.output, workspace)
            if not fixed:
                print(f"  ⚠️  Agent could not fix [{gate.name}] — stopping retries")
                break

            gate_result_new = _run_single_gate(gate, workspace, timeout_per_gate)
            gate_result.passed = gate_result_new.passed
            gate_result.exit_code = gate_result_new.exit_code
            gate_result.output = gate_result_new.output

        pipeline.gate_results.append(gate_result)

        if not gate_result.passed:
            pipeline.passed = False
            pipeline.stopped_at = gate.name
            icon = "❌"
            if not gate.retryable:
                print(f"  {icon} Gate [{gate.name}] failed (not retryable) — pipeline stopped")
            else:
                print(f"  {icon} Gate [{gate.name}] failed after {gate_result.attempts} attempts — pipeline stopped")
            break

        print(f"  ✅ Gate [{gate.name}] passed")

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
