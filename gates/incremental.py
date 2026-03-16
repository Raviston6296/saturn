"""
Incremental validation — maps changed files to modules and generates
targeted gate commands.

Workflow (from spec):
  1. Compute the diff (changed files)
  2. Map file paths → modules via module_mapping (or auto-detect for zdpas)
  3. Run gates only for the affected modules (faster feedback)
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from config import settings
from gates.config import RulesConfig, GateDef


# ════════════════════════════════════════════════════════════════════════════
# ZDPAS Module Mapping (built-in, no config needed)
# ════════════════════════════════════════════════════════════════════════════
ZDPAS_MODULE_MAPPING = {
    "source/com/zoho/dpaas/transformer": "transformer",
    "source/com/zoho/dpaas/dataframe": "dataframe",
    "source/com/zoho/dpaas/storage": "storage",
    "source/com/zoho/dpaas/util": "util",
    "source/com/zoho/dpaas/context": "context",
    "source/com/zoho/dpaas/query": "query",
    "source/com/zoho/dpaas/udf": "udf",
    "source/com/zoho/dpaas/udaf": "udaf",
    "source/com/zoho/dpaas/common": "common",
    "source/com/zoho/dpaas/parser": "parser",
    "source/com/zoho/dpaas/callback": "callback",
    "source/com/zoho/dpaas/widgets": "widgets",
    "source/com/zoho/dpaas/redis": "redis",
    "source/com/zoho/dpaas/parquet": "parquet",
    "source/com/zoho/dpaas/writer": "writer",
    "source/com/zoho/dpaas/sparkutil": "sparkutil",
    "source/com/zoho/dpaas/dfs": "dfs",
    "source/com/zoho/dpaas/job": "job",
    "source/com/zoho/dpaas/processor": "processor",
    "source/com/zoho/dpaas/datatype": "datatype",
    "source/com/zoho/dpaas/exception": "exception",
    "source/com/zoho/dpaas/logging": "logging",
    "source/com/zoho/dpaas/metrics": "metrics",
    "source/com/zoho/dpaas/ruleset": "ruleset",
    "source/com/zoho/dpaas/importutil": "importutil",
    "source/com/zoho/dpaas/migrator": "migrator",
    "source/com/zoho/dpaas/pdc": "pdc",
    "source/com/zoho/dpaas/zdfs": "zdfs",
    "source/com/zoho/dpaas/dfsimpl": "dfsimpl",
    # Test files also map to modules
    "test/source/com/zoho/dpaas/transformer": "transformer",
    "test/source/com/zoho/dpaas/dataframe": "dataframe",
    "test/source/com/zoho/dpaas/storage": "storage",
    "test/source/com/zoho/dpaas/util": "util",
    "test/source/com/zoho/dpaas/context": "context",
    "test/source/com/zoho/dpaas/query": "query",
    "test/source/com/zoho/dpaas/udf": "udf",
}


def get_affected_modules(
    changed_files: list[str],
    rules: RulesConfig,
) -> set[str]:
    """
    Map changed file paths to module names.

    Uses rules.yaml module_mapping if configured,
    otherwise falls back to built-in ZDPAS_MODULE_MAPPING.
    """
    modules: set[str] = set()

    # Use rules.yaml if configured
    if rules.module_mappings:
        for filepath in changed_files:
            normalized = filepath.replace("\\", "/")
            for mapping in rules.module_mappings:
                prefix = mapping.path.rstrip("/")
                if normalized.startswith(prefix + "/") or normalized == prefix:
                    modules.add(mapping.module)
                    break
        return modules

    # Fall back to built-in ZDPAS mapping
    for filepath in changed_files:
        normalized = filepath.replace("\\", "/")
        for prefix, module in ZDPAS_MODULE_MAPPING.items():
            if normalized.startswith(prefix + "/") or normalized.startswith(prefix):
                modules.add(module)
                break

    return modules


def get_affected_modules_zdpas(changed_files: list[str]) -> set[str]:
    """
    Get affected modules for ZDPAS specifically.
    This is used by the agent to know which modules to test.
    """
    modules: set[str] = set()

    for filepath in changed_files:
        normalized = filepath.replace("\\", "/")
        for prefix, module in ZDPAS_MODULE_MAPPING.items():
            if normalized.startswith(prefix + "/") or normalized.startswith(prefix):
                modules.add(module)
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
      {modules}      → space-separated module names
      {test_patterns} → space-separated test patterns
    """
    if not rules.module_mappings:
        return gates

    affected = get_affected_modules(changed_files, rules)
    if not affected:
        return gates

    test_patterns = get_test_patterns_for_modules(affected, rules)

    modules_str = " ".join(sorted(affected))
    patterns_str = " ".join(test_patterns) if test_patterns else ""

    targeted: list[GateDef] = []
    for gate in gates:
        cmd = gate.command
        cmd = cmd.replace("{modules}", modules_str)
        cmd = cmd.replace("{test_patterns}", patterns_str)

        targeted.append(GateDef(
            name=gate.name,
            description=gate.description,
            command=cmd,
            retryable=gate.retryable,
        ))

    return targeted


def get_changed_files_vs_base(
    workspace: str | Path,
    base_ref: str | None = None,
) -> list[str]:
    """
    Get changed files relative to a base ref.

    By default we diff against the repo's default branch (settings.gitlab_default_branch)
    so that all iterations of a Saturn task see the delta vs the MR target branch,
    not just vs the last local commit.
    """
    if base_ref is None:
        base_ref = settings.gitlab_default_branch or "HEAD"
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
