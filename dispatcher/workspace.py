"""
Workspace isolation — clone target repos into temp directories.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from config import settings


class Workspace:
    """Manages an isolated workspace for a single agent task."""

    def __init__(self, task_id: str, repo_url: str, branch_name: str):
        self.task_id = task_id
        self.repo_url = repo_url
        self.branch_name = branch_name
        self.path: Path = settings.workspace_path / task_id

    def setup(self) -> Path:
        """Clone the repo and create a working branch."""
        if self.path.exists():
            shutil.rmtree(self.path)

        self.path.mkdir(parents=True, exist_ok=True)

        if self.repo_url:
            # Clone with depth 50 for reasonable history
            self._run(f"git clone --depth=50 {self.repo_url} {self.path}")
        else:
            # Initialize empty repo if no URL
            self._run(f"git init {self.path}")

        # Configure git identity for the agent
        self._run_in_workspace("git config user.name 'Saturn Bot'")
        self._run_in_workspace("git config user.email 'saturn@bot.dev'")

        # Create working branch
        if self.branch_name:
            self._run_in_workspace(f"git checkout -b {self.branch_name}")

        return self.path

    def cleanup(self):
        """Remove the workspace directory."""
        if self.path.exists():
            shutil.rmtree(self.path, ignore_errors=True)

    def _run(self, cmd: str) -> str:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed: {cmd}\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )
        return result.stdout.strip()

    def _run_in_workspace(self, cmd: str) -> str:
        result = subprocess.run(
            cmd, shell=True, cwd=self.path,
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed in workspace: {cmd}\n"
                f"stderr: {result.stderr}"
            )
        return result.stdout.strip()

