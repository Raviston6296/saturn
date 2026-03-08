"""
Context builder — generates live workspace snapshots for the agent brain.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class ContextBuilder:
    """Builds a comprehensive snapshot of the workspace state."""

    def __init__(self, workspace: str):
        self.workspace = Path(workspace).resolve()

    def build_snapshot(self) -> str:
        """
        Build a live snapshot of the workspace to inject into Claude.
        Gives the agent full awareness of:
        - File structure
        - Git state
        - Test health
        - Build configuration
        """
        sections: list[str] = []

        # Project structure
        sections.append("═══ FILE TREE ═══")
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

        # Git state
        sections.append("\n═══ GIT STATUS ═══")
        sections.append(self._run("git status --short 2>/dev/null || echo '(not a git repo)'"))

        sections.append("\n═══ RECENT COMMITS ═══")
        sections.append(self._run("git log --oneline -10 2>/dev/null || echo '(no commits)'"))

        sections.append("\n═══ CURRENT DIFF ═══")
        diff = self._run("git diff HEAD --stat 2>/dev/null || echo '(no diff)'")
        sections.append(diff[:2000])

        # Detect project type and show config
        sections.append("\n═══ PROJECT CONFIG ═══")
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

        # README if exists
        for readme in ["README.md", "README.rst", "README.txt", "README"]:
            if (self.workspace / readme).exists():
                sections.append(f"\n═══ {readme} (first 50 lines) ═══")
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

