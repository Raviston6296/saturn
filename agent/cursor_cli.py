"""
Cursor CLI wrapper — delegates all coding work to the Cursor Agent CLI.

The Cursor Agent CLI (`agent`) is a standalone binary that provides
AI-powered coding directly from the terminal.

Install:
    curl https://cursor.com/install -fsS | bash

Usage in Saturn:
    Saturn invokes `agent` in --print (headless) mode for CI/automation:

      agent --print --trust --workspace /path "Your task prompt here"

    Modes:
      --mode=agent  (default) Full tool access — reads, edits, runs commands
      --mode=ask    Read-only exploration — no file changes
      --mode=plan   Design approach before coding

Public API
----------
  CursorCLI.run(prompt, workspace)       → CursorResult   (agent mode)
  CursorCLI.run_query(question, ...)     → str            (ask mode)
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from config import settings


# ── Result dataclass ──────────────────────────────────────────────

@dataclass
class CursorResult:
    """Result from a Cursor CLI invocation."""
    output: str = ""
    exit_code: int = 0
    success: bool = True
    error: str = ""
    files_changed: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """Return the output as a summary (stripped of ANSI codes)."""
        return _strip_ansi(self.output)


# ── Main wrapper ──────────────────────────────────────────────────

class CursorCLI:
    """
    Wrapper around the Cursor Agent CLI (`agent`).

    The CLI is invoked via subprocess in --print (headless) mode.
    It handles file reading, editing, code generation, running commands —
    everything the old agentic loop (brain + tools) used to do.

    Key flags:
      --print          Headless mode for scripts/CI (no interactive TUI)
      --trust          Trust the workspace without prompting
      --workspace      Set the working directory
      --force/--yolo   Auto-approve all tool calls (no confirmations)
      --mode           agent (default) | ask | plan
    """

    def __init__(
        self,
        cursor_path: str = "",
        timeout: int = 0,
    ):
        self.cursor_path = self._resolve_cli_path(cursor_path or settings.cursor_cli_path)
        self.timeout = timeout or settings.cursor_timeout_seconds

        # Verify CLI is available
        self._verify_cli()

    @staticmethod
    def _resolve_cli_path(path: str) -> str:
        """
        Resolve the `agent` binary to an absolute path.

        The Cursor installer puts it at ~/.local/bin/agent, which may not
        be on $PATH when Saturn runs as a background service or from an IDE.
        """
        import shutil

        # If it's already an absolute path that exists, use it
        if os.path.isabs(path) and os.path.isfile(path):
            return path

        # Check if it's on the current PATH
        found = shutil.which(path)
        if found:
            return found

        # Check common install locations
        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, ".local", "bin", "agent"),
            os.path.join(home, ".local", "bin", "cursor-agent"),
            "/usr/local/bin/agent",
        ]
        for candidate in candidates:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

        # Return the original — _verify_cli will give a clear error
        return path

    def _verify_cli(self):
        """Check that the `agent` CLI binary exists and is executable."""
        try:
            result = subprocess.run(
                [self.cursor_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                env=self._build_env(),
            )
            version = (result.stdout.strip() or result.stderr.strip()) or "(unknown)"
            print(f"🖥️  Cursor Agent CLI: {self.cursor_path} ({version})")
        except FileNotFoundError:
            raise RuntimeError(
                f"Cursor Agent CLI not found at '{self.cursor_path}'. "
                f"Install it: curl https://cursor.com/install -fsS | bash\n"
                f"Then set CURSOR_CLI_PATH in saturn.env (e.g. CURSOR_CLI_PATH={os.path.expanduser('~/.local/bin/agent')})"
            )
        except subprocess.TimeoutExpired:
            print(f"⚠️  Cursor CLI version check timed out — proceeding anyway")

    def run(
        self,
        prompt: str,
        workspace: str,
        timeout: int | None = None,
        mode: str = "agent",
    ) -> CursorResult:
        """
        Run a coding task via Cursor Agent CLI in headless (--print) mode.

        The agent operates on the workspace directory, making file changes
        directly. It has full tool access: file read/write, terminal, search.

        Args:
            prompt: Natural language task description / instructions
            workspace: Path to the workspace (git worktree) to operate in
            timeout: Override timeout in seconds (default from settings)
            mode: "agent" (default, full access), "ask" (read-only), "plan"

        Returns:
            CursorResult with output, exit code, and changed files
        """
        timeout = timeout or self.timeout
        workspace = str(Path(workspace).resolve())

        try:
            # Build the command
            cmd = self._build_command(prompt, workspace, mode)

            print(f"  🖥️  Running `agent` in {workspace} (mode={mode})")
            print(f"  📝 Prompt: {prompt[:150]}{'...' if len(prompt) > 150 else ''}")

            # Snapshot files before to detect changes
            files_before = self._snapshot_files(workspace)

            # Run Cursor Agent CLI in headless mode
            result = subprocess.run(
                cmd,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._build_env(),
            )

            output = (result.stdout + result.stderr).strip()
            output = _strip_ansi(output)

            # Detect changed files
            files_after = self._snapshot_files(workspace)
            changed = self._detect_changes(files_before, files_after)

            if result.returncode != 0 and not output:
                return CursorResult(
                    output=output,
                    exit_code=result.returncode,
                    success=False,
                    error=f"agent exited with code {result.returncode}",
                    files_changed=changed,
                )

            return CursorResult(
                output=output,
                exit_code=result.returncode,
                success=True,
                files_changed=changed,
            )

        except subprocess.TimeoutExpired:
            return CursorResult(
                output="",
                exit_code=-1,
                success=False,
                error=f"agent timed out after {timeout}s",
            )
        except Exception as e:
            return CursorResult(
                output="",
                exit_code=-1,
                success=False,
                error=f"agent error: {e}",
            )

    def run_query(
        self,
        question: str,
        context: str,
        workspace: str,
        timeout: int | None = None,
    ) -> str:
        """
        Ask Cursor CLI a question about the codebase (read-only --mode=ask).

        Used by repo_indexer for code search Q&A — replaces ask_qwen().

        Args:
            question: Natural language question
            context: Retrieved code context from ChromaDB
            workspace: Path to the repo
            timeout: Override timeout in seconds

        Returns:
            Answer string from Cursor
        """
        prompt = (
            "Answer the following question about this codebase. "
            "Reference specific filenames and line numbers when possible. "
            "Use markdown formatting.\n\n"
            "## Codebase Context\n\n"
            f"{context}\n\n"
            "---\n\n"
            f"## Question\n{question}"
        )

        result = self.run(
            prompt,
            workspace,
            timeout=timeout or 120,
            mode="ask",  # read-only mode — no file changes
        )

        if not result.success:
            return f"❌ Cursor CLI error: {result.error}"

        return result.output

    # ── Private helpers ───────────────────────────────────────────

    def _build_command(
        self, prompt: str, workspace: str, mode: str = "agent"
    ) -> list[str]:
        """
        Build the Cursor Agent CLI command for headless execution.

        Command form:
          agent --print --trust --yolo --workspace <path> [--mode <mode>] "prompt"
        """
        cmd = [
            self.cursor_path,
            "--print",              # headless mode (no interactive TUI)
            "--trust",              # trust workspace without prompting
            "--yolo",               # auto-approve all tool calls
            "--workspace", workspace,
        ]

        if mode and mode != "agent":
            cmd.extend(["--mode", mode])

        # The prompt goes as positional argument(s) at the end
        cmd.append(prompt)

        return cmd

    def _build_env(self) -> dict[str, str]:
        """Build environment variables for the agent subprocess."""
        env = os.environ.copy()
        # Ensure ~/.local/bin is on PATH (where agent is installed)
        local_bin = os.path.expanduser("~/.local/bin")
        if local_bin not in env.get("PATH", ""):
            env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"
        return env

    def _snapshot_files(self, workspace: str) -> dict[str, float]:
        """Take a snapshot of file modification times in the workspace."""
        snapshot: dict[str, float] = {}
        ws_path = Path(workspace)
        try:
            for f in ws_path.rglob("*"):
                if f.is_file() and ".git" not in f.parts:
                    rel = str(f.relative_to(ws_path))
                    snapshot[rel] = f.stat().st_mtime
        except Exception:
            pass
        return snapshot

    def _detect_changes(
        self,
        before: dict[str, float],
        after: dict[str, float],
    ) -> list[str]:
        """Detect files that were added or modified."""
        changed: list[str] = []

        for path, mtime in after.items():
            if path.endswith(".jar"):
                continue # ignore .jar files (often large and frequently rewritten)
            if path not in before:
                changed.append(path)  # new file
            elif mtime > before[path]:
                changed.append(path)  # modified

        return sorted(changed)


# ── Utility ───────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from a string."""
    return _ANSI_RE.sub("", text)

