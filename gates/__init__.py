"""
Saturn Deterministic Gates — validates AI-generated code before MR creation.

Public API:
    GatePipeline(workspace, fix_callback=...).run() → GatePipelineResult

Full validation workflow (from spec):
    Task received
        → Agent edits code
        → Compute diff
        → Check risk rules
        → Run deterministic gates (with incremental narrowing)
        → pass → create MR
        → fail (retryable) → agent fixes → retry gates
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from config import settings
from gates.config import load_repo_config, SaturnRepoConfig
from gates.risk import check_risk, RiskVerdict
from gates.executor import run_gate_pipeline, PipelineResult, FixCallback
from gates.incremental import (
    get_changed_files_vs_base,
    build_targeted_gates,
    get_affected_modules,
    get_affected_modules_zdpas,
)


def setup_dpaas_environment(workspace: str | Path) -> bool:
    """
    Validate that the DPAAS runtime environment is reachable before gates run.

    The actual extraction of dpaas.tar.gz and population of DPAAS_HOME is
    performed by the explicit 'setup' gate (stage 1 of the ZDPAS pipeline).
    This function is a pre-flight check only.

    Returns True when the environment looks usable, False (with a warning)
    when critical variables are missing.
    """
    import os

    dpaas_home = os.environ.get("DPAAS_HOME", "").strip()
    if not dpaas_home:
        # Fall back to the configured value so tests/dev environments still work
        dpaas_home = settings.saturn_dpaas_home

    if not dpaas_home:
        print(
            "  ⚠️  DPAAS_HOME is not set. The 'setup' gate will fail.\n"
            "     Export it in the runner VM shell profile or in saturn.env:\n"
            "       export DPAAS_HOME=/opt/dpaas"
        )
        return False

    print(f"  ✅ DPAAS_HOME: {dpaas_home}")
    return True


@dataclass
class GatePipelineResult:
    """Combined result of risk check + gate execution."""
    risk: RiskVerdict = field(default_factory=RiskVerdict)
    gates: PipelineResult = field(default_factory=PipelineResult)
    has_config: bool = False
    changed_files: list[str] = field(default_factory=list)
    affected_modules: set[str] = field(default_factory=set)
    skipped: bool = False
    skip_reason: str = ""

    @property
    def passed(self) -> bool:
        if self.skipped:
            return True
        return self.risk.passed and self.gates.passed

    @property
    def summary(self) -> str:
        lines = []
        if self.skipped:
            lines.append(f"⏭️  Gates skipped: {self.skip_reason}")
            return "\n".join(lines)

        lines.append(f"📋 Changed files: {len(self.changed_files)}")
        if self.affected_modules:
            lines.append(f"📦 Affected modules: {', '.join(sorted(self.affected_modules))}")

        lines.append(f"\n🛡️  Risk: {'✅ passed' if self.risk.passed else '❌ BLOCKED'}")
        if not self.risk.passed:
            lines.append(self.risk.summary)

        if self.gates.gate_results:
            lines.append(f"\n🚧 Gates:")
            lines.append(self.gates.summary)

        return "\n".join(lines)


class GatePipeline:
    """
    Orchestrates the full validation flow:
      1. Load .saturn/ config from the workspace
      2. Compute diff + check risk
      3. Narrow gates to affected modules (incremental)
      4. Run gates sequentially (with retry via fix_callback)

    When ``goose_orchestrated=True`` (Goose mode), only Tier-1 static
    validation gates are executed.  Tier-2 unit tests are skipped because
    Goose already ran them during its coding loop via MCP
    (run_module_tests).  This keeps the final gate pass lightweight and
    prevents duplicate test runs.
    """

    def __init__(
        self,
        workspace: str | Path,
        fix_callback: FixCallback | None = None,
        max_retries: int = 5,
        timeout_per_gate: int = 120,
        goose_orchestrated: bool = False,
    ):
        self.workspace = str(Path(workspace).resolve())
        self.fix_callback = fix_callback
        self.max_retries = max_retries
        self.timeout_per_gate = timeout_per_gate
        self.goose_orchestrated = goose_orchestrated
        self.config: SaturnRepoConfig | None = None

    def run(self) -> GatePipelineResult:
        """
        Execute the full validation pipeline.

        Gates ALWAYS run:
          - If .saturn/ exists → use repo-defined gates
          - If .saturn/ is missing → auto-discover project type and use defaults

        When goose_orchestrated=True:
          - Only Tier-1 (static) gates execute.
          - Tier-2 (unit) and Tier-3 (integration) gates are skipped.
        """
        result = GatePipelineResult()

        # 0. Setup Saturn's isolated DPAAS environment
        print("  🔧 Setting up Saturn DPAAS environment...")
        setup_dpaas_environment(self.workspace)

        # 1. Load config (repo-defined or auto-discovered defaults)
        self.config = load_repo_config(self.workspace)
        result.has_config = self.config.has_config

        if self.config.has_config:
            print("  📂 Using .saturn/ repo config")
        else:
            print("  🔍 Using auto-discovered defaults")

        if not self.config.gates.gates:
            result.skipped = True
            result.skip_reason = "No gates could be determined (unknown project type)"
            print("  ⚠️  No gates found — skipping validation")
            return result

        # 2. Compute diff
        changed_files = get_changed_files_vs_base(self.workspace)
        result.changed_files = changed_files

        if not changed_files:
            result.skipped = True
            result.skip_reason = "No files changed"
            print("  ℹ️  No files changed — gates skipped")
            return result

        print(f"  📋 {len(changed_files)} files changed")

        # 3. Risk check
        print("  🛡️  Checking patch risk...")
        result.risk = check_risk(
            self.workspace, self.config.risk, changed_files
        )
        if not result.risk.passed:
            print(f"  ❌ Risk check BLOCKED the patch:")
            for v in result.risk.violations:
                print(f"     • {v}")
            return result

        print("  ✅ Risk check passed")

        # 4. Incremental narrowing — detect affected modules
        # For ZDPAS: auto-detect modules from changed files (no config needed)
        # For other projects: use rules.yaml if configured
        if self.config.rules.module_mappings:
            affected = get_affected_modules(changed_files, self.config.rules)
        else:
            # Auto-detect for ZDPAS
            affected = get_affected_modules_zdpas(changed_files)

        result.affected_modules = affected
        if affected:
            print(f"  📦 Affected modules: {', '.join(sorted(affected))}")

        gates_to_run = build_targeted_gates(
            self.config.gates.gates,
            changed_files,
            self.config.rules,
        )

        # 5. Goose-orchestrated mode: skip Tier-2/Tier-3 gates
        #    Goose already ran compile_quick (Tier 1) and run_module_tests
        #    (Tier 2) via the Saturn MCP extension during its coding loop.
        #    Only Tier-1 static validation gates need to run here.
        if self.goose_orchestrated:
            tier1_gates = [g for g in gates_to_run if g.tier == 1]
            skipped_count = len(gates_to_run) - len(tier1_gates)
            if skipped_count:
                print(
                    f"  🪿  Goose-orchestrated: skipping {skipped_count} Tier-2/3 "
                    "gate(s) (Goose ran them via MCP)"
                )
            gates_to_run = tier1_gates

        # 6. Run gates
        print(f"  🚧 Running {len(gates_to_run)} gates...")
        result.gates = run_gate_pipeline(
            gates=gates_to_run,
            workspace=self.workspace,
            fix_callback=self.fix_callback,
            max_retries=self.max_retries,
            timeout_per_gate=self.timeout_per_gate,
            affected_modules=result.affected_modules,
        )

        if result.gates.passed:
            print("  ✅ All gates passed")
        else:
            print(f"  ❌ Gate pipeline failed at: {result.gates.stopped_at}")

        return result
