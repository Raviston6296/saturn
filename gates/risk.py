"""
Patch risk checker — enforces safety limits before gates run.

Blocks dangerous patches:
  - Too many files changed
  - Too many lines changed
  - Modifications to restricted paths/files
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from gates.config import RiskConfig


@dataclass
class RiskVerdict:
    """Result of a risk check."""
    passed: bool = True
    violations: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if self.passed:
            return "Risk check passed"
        return "Risk check FAILED:\n" + "\n".join(f"  • {v}" for v in self.violations)


def check_risk(
    workspace: str | Path,
    risk_config: RiskConfig,
    changed_files: list[str] | None = None,
) -> RiskVerdict:
    """
    Validate a patch against risk limits.

    Computes the diff from the worktree and checks:
      1. Number of files changed <= max_files_changed
      2. Number of lines changed <= max_lines_changed
      3. No modifications to restricted paths
      4. No modifications to restricted files
    """
    workspace = Path(workspace)
    verdict = RiskVerdict()

    if changed_files is None:
        changed_files = _get_changed_files(workspace)

    if not changed_files:
        return verdict

    # 1. Max files changed
    if len(changed_files) > risk_config.max_files_changed:
        verdict.passed = False
        verdict.violations.append(
            f"Files changed: {len(changed_files)} (limit: {risk_config.max_files_changed})"
        )

    # 2. Max lines changed
    lines_changed = _count_lines_changed(workspace)
    if lines_changed > risk_config.max_lines_changed:
        verdict.passed = False
        verdict.violations.append(
            f"Lines changed: {lines_changed} (limit: {risk_config.max_lines_changed})"
        )

    # 3. Restricted paths
    for filepath in changed_files:
        for restricted in risk_config.restricted_paths:
            restricted = restricted.rstrip("/")
            if filepath.startswith(restricted + "/") or filepath == restricted:
                verdict.passed = False
                verdict.violations.append(
                    f"Restricted path modified: {filepath} (policy blocks: {restricted}/)"
                )

    # 4. Restricted files
    for filepath in changed_files:
        basename = Path(filepath).name
        if filepath in risk_config.restricted_files or basename in risk_config.restricted_files:
            verdict.passed = False
            verdict.violations.append(
                f"Restricted file modified: {filepath}"
            )

    return verdict


def _get_changed_files(workspace: Path) -> list[str]:
    """Get list of changed files vs HEAD."""
    try:
        result = subprocess.run(
            "git diff --name-only HEAD",
            shell=True, cwd=workspace,
            capture_output=True, text=True, timeout=30,
        )
        staged = subprocess.run(
            "git diff --cached --name-only",
            shell=True, cwd=workspace,
            capture_output=True, text=True, timeout=30,
        )
        untracked = subprocess.run(
            "git ls-files --others --exclude-standard",
            shell=True, cwd=workspace,
            capture_output=True, text=True, timeout=30,
        )
        files = set()
        for out in [result.stdout, staged.stdout, untracked.stdout]:
            for line in out.strip().splitlines():
                if line.strip():
                    files.add(line.strip())
        return sorted(files)
    except Exception:
        return []


def _count_lines_changed(workspace: Path) -> int:
    """Count total lines added + removed."""
    try:
        result = subprocess.run(
            "git diff HEAD --numstat",
            shell=True, cwd=workspace,
            capture_output=True, text=True, timeout=30,
        )
        total = 0
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                added = int(parts[0]) if parts[0] != "-" else 0
                removed = int(parts[1]) if parts[1] != "-" else 0
                total += added + removed
        return total
    except Exception:
        return 0
