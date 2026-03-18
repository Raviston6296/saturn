"""
Context builder — generates workspace + repo-level snapshots for the agent brain.

Since Saturn maintains a persistent bare clone, we can provide deep
repo awareness (branches, history, contributors) on top of the
worktree-level file tree and git status.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dispatcher.workspace import RepoManager


class ContextBuilder:
    """Builds comprehensive snapshots of the workspace and repo state."""

    def __init__(self, workspace: str, repo_manager: "RepoManager | None" = None):
        self.workspace = Path(workspace).resolve()
        self.repo_manager = repo_manager

    def build_snapshot(self) -> str:
        """
        Build a full snapshot combining:
        1. Repo-level awareness (from bare clone — persistent knowledge)
        2. Worktree-level state (current files, git status, tests)
        """
        sections: list[str] = []

        # ── Repo-level context (deep knowledge from bare clone) ──
        if self.repo_manager:
            sections.append(self._build_repo_context())

        # ── Worktree-level context (current task workspace) ──
        sections.append(self._build_worktree_context())

        return "\n".join(sections)

    def _build_repo_context(self) -> str:
        """Build repo-level context from the persistent bare clone."""
        parts = ["═══ REPO KNOWLEDGE (persistent) ═══"]

        try:
            # File tree from HEAD (read directly from bare — no checkout needed)
            file_tree = self.repo_manager.get_file_tree()
            parts.append(f"\n── Full repo file tree ──\n{file_tree}")

            # Recent commits on default branch
            commits = self.repo_manager.get_recent_commits(15)
            parts.append(f"\n── Recent commits ──\n{commits}")

            # Active branches
            branches = self.repo_manager.get_branches()
            parts.append(f"\n── Remote branches ──\n{branches}")

            # Tags
            tags = self.repo_manager.get_tags(5)
            if tags:
                parts.append(f"\n── Recent tags ──\n{tags}")

            # Contributors
            contributors = self.repo_manager.get_contributors(5)
            if contributors:
                parts.append(f"\n── Top contributors ──\n{contributors}")

        except Exception as e:
            parts.append(f"\n(repo context error: {e})")

        return "\n".join(parts)

    def _build_worktree_context(self) -> str:
        """Build worktree-level context (current task workspace)."""
        sections = ["\n═══ WORKTREE STATE (current task) ═══"]

        # File tree (local working state)
        sections.append("\n── Working files ──")
        sections.append(self._run(
            "find . -type f"
            " -not -path '*/node_modules/*'"
            " -not -path '*/.git/*'"
            " -not -path '*/__pycache__/*'"
            " -not -path '*/dist/*'"
            " -not -path '*/.venv/*'"
            " -not -path '*/venv/*'"
            " -not -path '*/.idea/*'"
            " | sort | head -100"
        ))

        # Git status in this worktree
        sections.append("\n── Git status ──")
        sections.append(self._run("git status --short 2>/dev/null || echo '(not a git repo)'"))

        # Current branch
        sections.append("\n── Current branch ──")
        sections.append(self._run("git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '(unknown)'"))

        # Diff
        sections.append("\n── Current diff ──")
        diff = self._run("git diff HEAD --stat 2>/dev/null || echo '(no diff)'")
        sections.append(diff[:2000])

        # Detect project type
        sections.append("\n── Project config ──")
        if (self.workspace / "package.json").exists():
            sections.append("Type: Node.js")
            sections.append(self._run("cat package.json | head -30"))
        elif (self.workspace / "pyproject.toml").exists():
            sections.append("Type: Python (pyproject.toml)")
            sections.append(self._run("cat pyproject.toml | head -30"))
        elif (self.workspace / "requirements.txt").exists():
            sections.append("Type: Python (requirements.txt)")
            sections.append(self._run("cat requirements.txt"))
        elif (self.workspace / "pom.xml").exists():
            sections.append("Type: Java (Maven)")
        elif (self.workspace / "go.mod").exists():
            sections.append("Type: Go")
            sections.append(self._run("cat go.mod | head -15"))
        else:
            sections.append("Type: Unknown — check files manually")

        # README
        for readme in ["README.md", "README.rst", "README.txt", "README"]:
            if (self.workspace / readme).exists():
                sections.append(f"\n── {readme} (first 50 lines) ──")
                sections.append(self._run(f"head -50 {readme}"))
                break

        return "\n".join(sections)

    def get_test_status(self) -> str:
        """Run the project's test suite and return results."""
        if (self.workspace / "package.json").exists():
            return self._run("npm test -- --passWithNoTests 2>&1 | tail -20")
        elif (self.workspace / "pytest.ini").exists() or \
             (self.workspace / "pyproject.toml").exists() or \
             (self.workspace / "setup.py").exists():
            return self._run("python -m pytest -q --tb=short 2>&1 | tail -20")
        elif (self.workspace / "go.mod").exists():
            return self._run("go test ./... 2>&1 | tail -20")
        return "(no test runner detected)"

    def _run(self, cmd: str) -> str:
        try:
            result = subprocess.run(
                cmd, shell=True, cwd=self.workspace,
                capture_output=True, text=True, timeout=60,
            )
            output = (result.stdout + result.stderr).strip()
            return output or "(empty)"
        except subprocess.TimeoutExpired:
            return "(command timed out)"
        except Exception as e:
            return f"(error: {e})"

