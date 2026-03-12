"""
Incremental validation — maps changed files to modules and generates
targeted gate commands.

Workflow (from spec):
  1. Compute the diff (changed files)
  2. Map file paths → modules via module_mapping
  3. Run gates only for the affected modules (faster feedback)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from gates.config import RulesConfig, GateDef


def get_affected_modules(
    changed_files: list[str],
    rules: RulesConfig,
) -> set[str]:
    """
    Map changed file paths to module names using rules.yaml module_mapping.
    Returns a set of affected module names.
    """
    modules: set[str] = set()

    for filepath in changed_files:
        normalized = filepath.replace("\\", "/")
        for mapping in rules.module_mappings:
            prefix = mapping.path.rstrip("/")
            if normalized.startswith(prefix + "/") or normalized == prefix:
                modules.add(mapping.module)
                break

    return modules


def get_test_patterns_for_modules(
    modules: set[str],
    rules: RulesConfig,
) -> list[str]:
    """Get test patterns for the affected modules."""
    patterns = []
    for tm in rules.test_mappings:
        if tm.module in modules:
            patterns.append(tm.pattern)
    return patterns


def build_targeted_gates(
    gates: list[GateDef],
    changed_files: list[str],
    rules: RulesConfig,
) -> list[GateDef]:
    """
    Narrow gate commands to only affected modules when possible.

    If no module_mapping is configured, or changed files don't match
    any mapping, returns the original gates unchanged.

    Substitution tokens in gate commands:
      {modules}       → space-separated module names
      {test_patterns} → space-separated test patterns
      {test_w_flags}  → ScalaTest -w flags: -w com.foo -w com.bar
    """
    if not rules.module_mappings:
        return gates

    affected = get_affected_modules(changed_files, rules)
    if not affected:
        return gates

    test_patterns = get_test_patterns_for_modules(affected, rules)

    modules_str = " ".join(sorted(affected))
    patterns_str = " ".join(test_patterns) if test_patterns else ""

    # ScalaTest -w flag format (strips trailing .* from package patterns)
    w_flags = (
        " ".join(f"-w {p.removesuffix('.*')}" for p in test_patterns)
        if test_patterns else ""
    )

    targeted: list[GateDef] = []
    for gate in gates:
        cmd = gate.command
        cmd = cmd.replace("{modules}", modules_str)
        cmd = cmd.replace("{test_patterns}", patterns_str)
        cmd = cmd.replace("{test_w_flags}", w_flags)

        targeted.append(GateDef(
            name=gate.name,
            description=gate.description,
            command=cmd,
            retryable=gate.retryable,
        ))

    return targeted


def get_changed_files_vs_base(
    workspace: str | Path,
    base_ref: str = "HEAD",
) -> list[str]:
    """Get changed files relative to a base ref."""
    try:
        result = subprocess.run(
            f"git diff --name-only {base_ref}",
            shell=True, cwd=str(workspace),
            capture_output=True, text=True, timeout=30,
        )
        untracked = subprocess.run(
            "git ls-files --others --exclude-standard",
            shell=True, cwd=str(workspace),
            capture_output=True, text=True, timeout=30,
        )
        files = set()
        for out in [result.stdout, untracked.stdout]:
            for line in out.strip().splitlines():
                if line.strip():
                    files.add(line.strip())
        return sorted(files)
    except Exception:
        return []
