"""
Git tools — branch, commit, push operations via subprocess.
"""

from __future__ import annotations

import subprocess
import pathlib


class GitTools:
    """Git operations within the workspace."""

    def __init__(self, workspace: str):
        self.workspace = pathlib.Path(workspace).resolve()

    def status(self) -> str:
        """Show current git status."""
        return self._run("git status --short")

    def diff(self, file_path: str | None = None) -> str:
        """Show current diff."""
        cmd = "git diff HEAD"
        if file_path:
            cmd += f" -- {file_path}"
        output = self._run(cmd)
        # Cap diff output to prevent token explosion
        if len(output) > 6000:
            output = output[:6000] + "\n\n... [DIFF TRUNCATED] ..."
        return output

    def log(self, count: int = 10) -> str:
        """Show recent commit log."""
        return self._run(f"git log --oneline -{count}")

    def commit(self, message: str) -> str:
        """Stage all changes and commit."""
        # Stage everything
        stage_result = self._run("git add -A")

        # Check there's something to commit
        status = self._run("git status --short")
        if not status.strip():
            return "NOTHING TO COMMIT: Working tree is clean."

        # Commit
        result = self._run(f'git commit -m "{message}"')
        return f"STAGED:\n{status}\n\nCOMMIT:\n{result}"

    def push(self) -> str:
        """Push current branch to origin.

        Uses --force for saturn/ branches because they are recreated fresh
        from the default branch for each task via `git worktree add -B`.
        The local branch has no remote tracking info, so --force-with-lease
        would fail. Since saturn/ branches are owned exclusively by the bot,
        --force is safe here.
        """
        branch = self._run("git rev-parse --abbrev-ref HEAD").strip()
        force_flag = " --force" if branch.startswith("saturn/") else ""
        result = self._run(f"git push -u origin {branch}{force_flag}", timeout=120)
        return f"PUSHED branch '{branch}' to origin\n{result}"

    def create_branch(self, branch_name: str) -> str:
        """Create and switch to a new branch."""
        result = self._run(f"git checkout -b {branch_name}")
        return result

    def _run(self, cmd: str, timeout: int = 60) -> str:
        """Run a git command in the workspace."""
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (result.stdout + result.stderr).strip()
            if result.returncode != 0 and not output:
                return f"ERROR (exit {result.returncode}): {cmd}"
            return output or "(empty)"
        except subprocess.TimeoutExpired:
            return f"ERROR: Git command timed out: {cmd}"
        except Exception as e:
            return f"ERROR: {e}"

