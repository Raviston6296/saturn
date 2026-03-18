"""
Goose CLI wrapper — delegates all coding work to the Goose AI agent.

Goose (by Block) is an open-source, extensible AI coding agent that runs
from the terminal with full tool access: file read/write, shell, web search.

Install:
    # macOS / Linux
    curl -fsSL https://github.com/block/goose/releases/latest/download/goose-installer.sh | bash

    # Or via pip
    pip install goose-ai

Usage in Saturn:
    Saturn invokes `goose run --text "prompt" --with-builtin developer`
    in the workspace directory (non-interactive headless mode).

Configuration (saturn.env):
    LLM_PROVIDER=goose
    GOOSE_CLI_PATH=goose           # goose binary (must be on PATH)
    GOOSE_TIMEOUT_SECONDS=600      # max time per invocation
    GOOSE_PROVIDER=anthropic       # model provider (anthropic, openai, ollama, …)
    GOOSE_MODEL=claude-3-5-sonnet-20241022  # model name

Environment variables forwarded to Goose:
    GOOSE_PROVIDER, GOOSE_MODEL, ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.
    are forwarded from the Saturn environment to the Goose subprocess.

Public API
----------
  GooseCLI.run(prompt, workspace)       → GooseResult   (full agent mode)
  GooseCLI.run_query(question, ...)     → str            (read-only mode)
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
class GooseResult:
    """Result from a Goose CLI invocation."""
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

class GooseCLI:
    """
    Wrapper around the Goose AI coding agent CLI.

    Goose is invoked via `goose run --text "prompt" --with-builtin developer`
    in the workspace directory (headless, non-interactive).

    Key differences from Cursor CLI:
      - No `--workspace` flag — Goose uses the current working directory (cwd)
      - Model provider and model are configured via environment variables
      - `--with-builtin developer` enables full tool access (file I/O, shell)

    Example command:
      goose run --text "Fix the ZDFilter bug" --with-builtin developer
    """

    def __init__(
        self,
        goose_path: str = "",
        timeout: int = 0,
    ):
        self.goose_path = self._resolve_cli_path(goose_path or settings.goose_cli_path)
        self.timeout = timeout or settings.goose_timeout_seconds

        # Verify CLI is available
        self._verify_cli()

    @staticmethod
    def _resolve_cli_path(path: str) -> str:
        """
        Resolve the `goose` binary to an absolute path.

        Goose installer puts the binary in ~/.local/bin/goose or /usr/local/bin/goose.
        """
        import shutil

        if os.path.isabs(path) and os.path.isfile(path):
            return path

        found = shutil.which(path)
        if found:
            return found

        home = os.path.expanduser("~")
        candidates = [
            os.path.join(home, ".local", "bin", "goose"),
            "/usr/local/bin/goose",
            "/usr/bin/goose",
        ]
        for candidate in candidates:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate

        return path  # _verify_cli will give a clear error

    def _verify_cli(self):
        """Check that the `goose` CLI binary exists and is executable."""
        try:
            result = subprocess.run(
                [self.goose_path, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                env=self._build_env(),
            )
            version = (result.stdout.strip() or result.stderr.strip()) or "(unknown)"
            print(f"🪿  Goose CLI: {self.goose_path} ({version})")
        except FileNotFoundError:
            raise RuntimeError(
                f"Goose CLI not found at '{self.goose_path}'. "
                f"Install: curl -fsSL https://github.com/block/goose/releases/latest/download/goose-installer.sh | bash\n"
                f"Then set GOOSE_CLI_PATH in saturn.env"
            )
        except subprocess.TimeoutExpired:
            print(f"⚠️  Goose CLI version check timed out — proceeding anyway")

    def run(
        self,
        prompt: str,
        workspace: str,
        timeout: int | None = None,
    ) -> GooseResult:
        """
        Run a coding task via Goose CLI in headless (non-interactive) mode.

        Goose operates in the workspace directory, making file changes directly.
        It has full tool access via the 'developer' built-in: file I/O, shell,
        code analysis.

        Args:
            prompt: Natural language task description / instructions
            workspace: Path to the workspace (git worktree) to operate in
            timeout: Override timeout in seconds (default from settings)

        Returns:
            GooseResult with output, exit code, and changed files
        """
        timeout = timeout or self.timeout
        workspace = str(Path(workspace).resolve())

        try:
            cmd = self._build_command(prompt)

            print(f"  🪿  Running Goose in {workspace}")
            print(f"  📝 Prompt: {prompt[:150]}{'...' if len(prompt) > 150 else ''}")

            # Snapshot files before to detect changes
            files_before = self._snapshot_files(workspace)

            result = subprocess.run(
                cmd,
                cwd=workspace,          # Goose uses cwd as workspace
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._build_env(),
            )

            output = (result.stdout + result.stderr).strip()
            output = _strip_ansi(output)

            files_after = self._snapshot_files(workspace)
            changed = self._detect_changes(files_before, files_after)

            if result.returncode != 0 and not output:
                return GooseResult(
                    output=output,
                    exit_code=result.returncode,
                    success=False,
                    error=f"goose exited with code {result.returncode}",
                    files_changed=changed,
                )

            return GooseResult(
                output=output,
                exit_code=result.returncode,
                success=True,
                files_changed=changed,
            )

        except subprocess.TimeoutExpired:
            return GooseResult(
                output="",
                exit_code=-1,
                success=False,
                error=f"goose timed out after {timeout}s",
            )
        except Exception as e:
            return GooseResult(
                output="",
                exit_code=-1,
                success=False,
                error=f"goose error: {e}",
            )

    def run_query(
        self,
        question: str,
        context: str,
        workspace: str,
        timeout: int | None = None,
    ) -> str:
        """
        Ask Goose a question about the codebase (read-only exploration).

        Args:
            question: Natural language question
            context: Retrieved code context
            workspace: Path to the repo
            timeout: Override timeout

        Returns:
            Answer string from Goose
        """
        prompt = (
            "Answer the following question about this codebase. "
            "Read files as needed but do NOT make any changes. "
            "Reference specific filenames and line numbers.\n\n"
            "## Codebase Context\n\n"
            f"{context}\n\n"
            "---\n\n"
            f"## Question\n{question}"
        )

        result = self.run(prompt, workspace, timeout=timeout or 120)

        if not result.success:
            return f"❌ Goose CLI error: {result.error}"

        return result.output

    # ── Private helpers ───────────────────────────────────────────

    def _build_command(self, prompt: str, profile: str = "") -> list[str]:
        """
        Build the Goose CLI command for headless execution.

        When a Saturn profile exists the command includes ``--profile`` so
        the MCP extension (saturn-zdpas) is automatically loaded.  Falls back
        to ``--with-builtin developer`` only when no profile is available.

        Command form (with profile):
          goose run --profile saturn-zdpas --text "prompt"

        Command form (without profile — fallback):
          goose run --text "prompt" --with-builtin developer
        """
        if profile:
            return [
                self.goose_path,
                "run",
                "--profile", profile,
                "--text", prompt,
            ]

        return [
            self.goose_path,
            "run",
            "--text", prompt,
            "--with-builtin", "developer",
        ]

    def _build_env(self) -> dict[str, str]:
        """Build environment variables for the Goose subprocess."""
        env = os.environ.copy()

        # Ensure ~/.local/bin is on PATH (common Goose install location)
        local_bin = os.path.expanduser("~/.local/bin")
        if local_bin not in env.get("PATH", ""):
            env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"

        # Saturn project root must be on PYTHONPATH so Goose extensions
        # (saturn-zdpas MCP) can find the mcp package via env_keys passthrough.
        saturn_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        existing_pp = env.get("PYTHONPATH", "")
        if saturn_root not in existing_pp:
            env["PYTHONPATH"] = f"{saturn_root}:{existing_pp}" if existing_pp else saturn_root

        # Forward Goose model configuration from Saturn settings
        if settings.goose_provider:
            env.setdefault("GOOSE_PROVIDER", settings.goose_provider)
        if settings.goose_model:
            env.setdefault("GOOSE_MODEL", settings.goose_model)

        return env

    # Directories / file patterns that are build/test artifacts, not code changes.
    _IGNORE_DIRS = frozenset({
        ".git", "unit_test", "target", "build", "out", ".bsp",
    })
    _IGNORE_FILES = frozenset({
        "test.out", "err.log", ".saturn_context.md",
    })
    _IGNORE_SUFFIXES = (".class", ".jar", ".log", ".xml")

    def _snapshot_files(self, workspace: str) -> dict[str, float]:
        """Take a snapshot of file modification times in the workspace."""
        snapshot: dict[str, float] = {}
        ws_path = Path(workspace)
        try:
            for f in ws_path.rglob("*"):
                if not f.is_file():
                    continue
                parts = f.parts
                if any(d in self._IGNORE_DIRS for d in parts):
                    continue
                if f.name in self._IGNORE_FILES:
                    continue
                if f.name.endswith(self._IGNORE_SUFFIXES):
                    continue
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
            if path not in before:
                changed.append(path)
            elif mtime > before[path]:
                changed.append(path)

        return sorted(changed)


# ── Utility ───────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from a string."""
    return _ANSI_RE.sub("", text)
