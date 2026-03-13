"""
Enhanced Goose Agent for Saturn.

This replaces the simple GooseCLI subprocess call with a full-featured
Goose session orchestrator:

  1. Session management — one named session per task; Goose keeps context
     across all fix attempts in a single task (no context loss on retry)

  2. Real-time streaming — Goose output is streamed line-by-line so the
     user sees progress immediately instead of waiting for completion

  3. Rich ZDPAS context injection — before every invocation, Saturn injects:
     - Changed files and affected modules
     - ZDPAS project structure overview
     - Recent task history from memory
     - Gate-specific error analysis (parsed compile errors / test failures)

  4. Structured fix prompts — when a gate fails the agent produces a
     targeted prompt that includes:
     - Exact failing file and line number (parsed from scalac output)
     - Which tests failed and what they expected vs got
     - What module is affected and which files to focus on

  5. Custom ZDPAS toolkit — injects SaturnZDPASTools context into prompts
     (does NOT require goose-ai library; Saturn runs the tools itself and
     includes the results in the Goose prompt)

Architecture:

  GooseAgent.run(task)
      → _build_rich_prompt(task, context)
      → _stream_goose(prompt, session)   ← Popen + readline loop
          → prints streaming output
          → collects all lines
          → detects changed files
      → GooseResult

  GooseAgent.fix(gate_name, error, session)
      → _analyze_error(gate_name, error)  ← SaturnZDPASTools.parse_*
      → _build_fix_prompt(gate_name, analysis, context)
      → _stream_goose(fix_prompt, session)   ← same session → memory kept
      → GooseResult

Design note on sessions
-----------------------
Goose sessions are named (--session <name>) and persist on disk.
Saturn uses a per-task session name derived from the branch name:
    session = f"saturn-{branch_name[:30]}"

The session survives across gate retries so Goose remembers:
    "I already changed ZDFilter.scala; the compile error was a type mismatch
     on line 42; I changed the return type to Int. Now the test fails because
     the expected value changed from 5 to 3..."

This context is invaluable for multi-step self-healing.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from config import settings
from agent.goose_cli import GooseCLI, GooseResult, _strip_ansi
from saturn_goose.toolkit import SaturnZDPASTools


# ── Result types ──────────────────────────────────────────────────


@dataclass
class GooseAgentResult:
    """
    Result from a GooseAgent invocation.
    Extends GooseResult with ZDPAS-specific metadata.
    """
    output: str = ""
    exit_code: int = 0
    success: bool = True
    error: str = ""
    files_changed: list[str] = field(default_factory=list)

    # ZDPAS-specific
    session_name: str = ""
    affected_modules: set[str] = field(default_factory=set)
    stream_lines: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return _strip_ansi(self.output)

    def to_goose_result(self) -> GooseResult:
        """Convert to basic GooseResult for backward compatibility."""
        return GooseResult(
            output=self.output,
            exit_code=self.exit_code,
            success=self.success,
            error=self.error,
            files_changed=self.files_changed,
        )


# ── GooseAgent ────────────────────────────────────────────────────


class GooseAgent:
    """
    Enhanced Goose session orchestrator for Saturn.

    Key features over basic GooseCLI:
      - Named sessions (Goose keeps memory across fix retries)
      - Real-time streaming output
      - Rich ZDPAS context injection before every call
      - Error analysis using SaturnZDPASTools
      - Profile management (saturn-zdpas profile)

    Designed to be a drop-in replacement for GooseCLI in AutonomousAgent,
    but with substantially better performance on Scala/Java tasks.
    """

    def __init__(
        self,
        workspace: str,
        branch_name: str = "",
        goose_path: str = "",
        timeout: int = 0,
        stream: bool = True,
    ):
        self.workspace = str(Path(workspace).resolve())
        self.branch_name = branch_name
        self.stream = stream
        self.timeout = timeout or settings.goose_timeout_seconds

        # Session name — shared across all fix retries for this task
        # Sanitize branch name for use as session identifier
        safe_branch = "".join(c if c.isalnum() or c in "-_" else "-" for c in branch_name)
        self.session_name = f"saturn-{safe_branch[:30]}" if safe_branch else "saturn-task"

        # Set up CLI wrapper (for path resolution + version check)
        self._cli = GooseCLI(goose_path=goose_path, timeout=timeout)

        # ZDPAS tools for context injection
        self._tools = SaturnZDPASTools(workspace)

        # Ensure saturn-zdpas profile exists
        self._setup_profile()

        # Cache: project structure (expensive to recompute each call)
        self._project_structure: str | None = None

    def run(
        self,
        task: str,
        files_changed: list[str] | None = None,
        timeout: int | None = None,
    ) -> GooseAgentResult:
        """
        Run a coding task via Goose with rich ZDPAS context.

        Injects project structure, changed-file context, and task history
        into the prompt before invoking Goose.

        Args:
            task: Natural language task description
            files_changed: Files changed so far (for context injection)
            timeout: Override timeout in seconds

        Returns:
            GooseAgentResult with output, changed files, and session name
        """
        print(f"\n  🪿  GooseAgent.run() — session: {self.session_name}")

        # Build context-enriched prompt
        prompt = self._build_rich_prompt(task, files_changed or [])

        # Snapshot files to detect changes
        files_before = self._cli._snapshot_files(self.workspace)

        # Run Goose with streaming
        output_lines, exit_code = self._stream_goose(
            prompt=prompt,
            timeout=timeout,
        )

        output = "\n".join(output_lines)
        files_after = self._cli._snapshot_files(self.workspace)
        changed = self._cli._detect_changes(files_before, files_after)

        success = output_lines and exit_code == 0

        return GooseAgentResult(
            output=output,
            exit_code=exit_code,
            success=success,
            files_changed=changed,
            session_name=self.session_name,
            stream_lines=output_lines,
        )

    def fix(
        self,
        gate_name: str,
        error_output: str,
        files_changed: list[str] | None = None,
        timeout: int | None = None,
    ) -> GooseAgentResult:
        """
        Fix a failing gate using the SAME Goose session.

        The key advantage: Goose already knows what it changed in `run()`.
        The fix prompt provides structured error analysis so Goose can
        focus on the exact problem rather than re-reading the whole codebase.

        Args:
            gate_name: Name of the failing gate ("compile", "unit-tests", etc.)
            error_output: Raw error output from the gate
            files_changed: Files changed so far
            timeout: Override timeout

        Returns:
            GooseAgentResult with the fix output and changed files
        """
        print(f"\n  🪿  GooseAgent.fix() — gate: {gate_name}, session: {self.session_name}")

        # Analyze the error into structured form
        analysis = self._analyze_error(gate_name, error_output)

        # Build targeted fix prompt
        prompt = self._build_fix_prompt(gate_name, analysis, error_output, files_changed or [])

        files_before = self._cli._snapshot_files(self.workspace)

        output_lines, exit_code = self._stream_goose(
            prompt=prompt,
            timeout=timeout,
        )

        output = "\n".join(output_lines)
        files_after = self._cli._snapshot_files(self.workspace)
        changed = self._cli._detect_changes(files_before, files_after)

        success = exit_code == 0 or bool(changed)  # Changed files = fix was applied

        return GooseAgentResult(
            output=output,
            exit_code=exit_code,
            success=success,
            files_changed=changed,
            session_name=self.session_name,
            stream_lines=output_lines,
        )

    def cleanup_session(self):
        """
        Remove the Goose session after the task completes.
        Sessions are stored on disk; clean up to avoid accumulation.
        """
        session_dir = Path.home() / ".config" / "goose" / "sessions"
        if session_dir.exists():
            for f in session_dir.glob(f"{self.session_name}*"):
                try:
                    f.unlink()
                except Exception:
                    pass

    # ── Private: prompt builders ──────────────────────────────────

    def _build_rich_prompt(self, task: str, files_changed: list[str]) -> str:
        """
        Build a context-enriched prompt for the initial task run.

        Includes:
          - Task description
          - ZDPAS project structure (cached after first call)
          - Changed files context (if files_changed provided)
          - Explicit instructions (no commit, no push)
        """
        sections = [f"# Task\n\n{task}\n"]

        # Project structure (cached)
        if self._project_structure is None:
            self._project_structure = self._tools.get_project_structure()
        sections.append(f"# Project Structure\n\n{self._project_structure}\n")

        # Changed files context
        if files_changed:
            ctx = self._tools.get_changed_file_context(files_changed)
            sections.append(f"# Changed Files\n\n{ctx}\n")

        sections.append(
            "# Instructions\n\n"
            "- Use the developer tools to read files before editing\n"
            "- Search for relevant files with `find` or read directory listings\n"
            "- Make all necessary changes to complete the task\n"
            "- For Scala: follow existing code style (2-space indent, case classes, etc.)\n"
            "- For tests: add test cases near similar ones in the Suite\n"
            "- Do NOT commit, push, or create merge requests — Saturn handles that\n"
            "- Do NOT run scalac or java — Saturn will compile after your changes\n"
        )

        return "\n".join(sections)

    def _build_fix_prompt(
        self,
        gate_name: str,
        analysis: str,
        raw_error: str,
        files_changed: list[str],
    ) -> str:
        """
        Build a targeted fix prompt for a failing gate.

        Uses structured error analysis from SaturnZDPASTools so Goose gets
        clean, actionable information instead of raw compiler/test output.
        """
        sections = [f"# Fix Required: Gate [{gate_name}] Failed\n"]

        # Structured error analysis
        sections.append(f"## Error Analysis\n\n{analysis}\n")

        # Changed files context
        if files_changed:
            ctx = self._tools.get_changed_file_context(files_changed)
            sections.append(f"## Changed Files\n\n{ctx}\n")

        # Gate-specific instructions
        if gate_name == "compile":
            sections.append(
                "## Fix Instructions\n\n"
                "The Scala/Java compilation failed. To fix:\n"
                "1. Read the exact file and line number from the error above\n"
                "2. Read the failing file to understand context\n"
                "3. Fix the type/syntax/import error\n"
                "4. Ensure your fix doesn't break other files in the module\n"
                "5. Common causes: wrong return type, missing import, wrong method signature\n"
            )
        elif gate_name in ("unit-tests", "build-test-jar"):
            sections.append(
                "## Fix Instructions\n\n"
                "Unit tests are failing. To fix:\n"
                "1. Identify which test suite and test case failed\n"
                "2. Read the test file to understand what it expects\n"
                "3. Fix the SOURCE code to match the expected behavior\n"
                "   (modify tests only if the task explicitly changes behavior)\n"
                "4. Check if any assertion values need updating\n"
                "5. Ensure your fix doesn't break other tests in the module\n"
            )
        else:
            sections.append(
                "## Fix Instructions\n\n"
                f"The [{gate_name}] gate failed. Please fix the code so this gate passes.\n"
                "All gates will be re-run from the beginning after your fix.\n"
            )

        sections.append(
            "## Important\n\n"
            "- Do NOT commit, push, or run compilation — Saturn handles that automatically\n"
            "- Focus only on the files mentioned in the error above\n"
            "- Make the minimal change needed to fix the specific error\n"
        )

        return "\n".join(sections)

    # ── Private: error analysis ───────────────────────────────────

    def _analyze_error(self, gate_name: str, error_output: str) -> str:
        """
        Analyze gate error output using SaturnZDPASTools.

        Returns structured analysis (not raw compiler output) so Goose
        can immediately focus on the exact problem.
        """
        if gate_name == "compile":
            return self._tools.parse_compile_errors(error_output)
        elif gate_name in ("unit-tests", "build-test-jar"):
            return self._tools.parse_test_failures(error_output)
        else:
            # Truncate for other gates
            lines = error_output.strip().splitlines()
            if len(lines) > 50:
                return (
                    "\n".join(lines[:10]) +
                    "\n\n... (truncated) ...\n\n" +
                    "\n".join(lines[-30:])
                )
            return error_output

    # ── Private: Goose subprocess ─────────────────────────────────

    def _stream_goose(
        self,
        prompt: str,
        timeout: int | None = None,
    ) -> tuple[list[str], int]:
        """
        Run Goose with a named session and stream output line-by-line.

        Uses subprocess.Popen instead of subprocess.run so we can read
        output as it arrives and show real-time progress.

        Returns: (output_lines, exit_code)
        """
        timeout = timeout or self.timeout
        env = self._build_env()

        cmd = [
            self._cli.goose_path,
            "run",
            "--session", self.session_name,
            "--text", prompt,
            "--with-builtin", "developer",
        ]

        print(f"     cmd: {' '.join(cmd[:4])} ... [prompt={len(prompt)} chars]")

        output_lines: list[str] = []
        start_time = time.time()

        try:
            process = subprocess.Popen(
                cmd,
                cwd=self.workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )

            assert process.stdout is not None

            if self.stream:
                # Stream output line by line
                for line in iter(process.stdout.readline, ""):
                    elapsed = time.time() - start_time
                    if elapsed > timeout:
                        process.kill()
                        output_lines.append(f"\n⚠️  Goose timed out after {timeout}s")
                        return output_lines, -1

                    clean = _strip_ansi(line)
                    output_lines.append(clean.rstrip())
                    print(f"  🪿  {clean}", end="", flush=True)
            else:
                # Non-streaming: wait for completion
                try:
                    stdout, _ = process.communicate(timeout=timeout)
                    output_lines = _strip_ansi(stdout).splitlines()
                except subprocess.TimeoutExpired:
                    process.kill()
                    output_lines.append(f"\n⚠️  Goose timed out after {timeout}s")
                    return output_lines, -1

            process.wait()
            return output_lines, process.returncode

        except FileNotFoundError:
            return [f"❌ Goose binary not found: {self._cli.goose_path}"], -1
        except Exception as e:
            return [f"❌ Goose error: {e}"], -1

    def _build_env(self) -> dict[str, str]:
        """Build environment for Goose subprocess."""
        return self._cli._build_env()

    def _setup_profile(self):
        """Setup the Saturn Goose profile (non-fatal if it fails)."""
        try:
            from agent.goose_profile import ensure_saturn_profile
            ensure_saturn_profile()
        except Exception as e:
            print(f"  ⚠️  Could not setup Goose profile: {e} — using defaults")
