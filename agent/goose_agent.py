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
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from config import settings
from agent.goose_cli import GooseCLI, GooseResult, _strip_ansi
from saturn_goose.toolkit import SaturnZDPASTools


# ── Summary parser ────────────────────────────────────────────────

@dataclass
class StructuredSummary:
    """Parsed structured summary from Goose output."""
    root_cause: str = ""
    changes: list[str] = field(default_factory=list)
    tests: str = ""

    @property
    def found(self) -> bool:
        return bool(self.root_cause or self.changes)

    def for_mr(self) -> str:
        """Render for GitLab MR description (Markdown)."""
        parts = []
        if self.root_cause:
            parts.append(f"### Root Cause\n\n{self.root_cause}\n")
        if self.changes:
            parts.append("### Changes\n\n" + "\n".join(f"- {c}" for c in self.changes) + "\n")
        if self.tests:
            parts.append(f"### Testing\n\n{self.tests}\n")
        return "\n".join(parts)

    def for_cliq(self) -> str:
        """Render for Cliq message (plain text, concise)."""
        parts = []
        if self.root_cause:
            parts.append(f"*Root Cause:* {self.root_cause}")
        if self.changes:
            parts.append("*Changes:*\n" + "\n".join(f"  • {c}" for c in self.changes))
        if self.tests:
            parts.append(f"*Tests:* {self.tests}")
        return "\n".join(parts)


def _parse_structured_summary(output: str) -> StructuredSummary:
    """
    Extract the SATURN_SUMMARY block from Goose output.
    Falls back to heuristic extraction from the last text block.
    """
    summary = StructuredSummary()

    # Try exact SATURN_SUMMARY block first
    match = re.search(
        r"SATURN_SUMMARY\s*\n(.*?)(?:```|$)",
        output,
        re.DOTALL,
    )
    if match:
        block = match.group(1).strip()
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("ROOT_CAUSE:"):
                summary.root_cause = line[len("ROOT_CAUSE:"):].strip()
            elif line.startswith("- "):
                summary.changes.append(line[2:].strip())
            elif line.startswith("TESTS:"):
                summary.tests = line[len("TESTS:"):].strip()
            elif line.startswith("CHANGES:"):
                continue  # header line, skip
        if summary.found:
            return summary

    # Fallback: extract from last substantial text section of Goose output
    # Look for common patterns in Goose's natural language summaries
    lines = output.strip().splitlines()
    tail = "\n".join(lines[-80:]) if len(lines) > 80 else output

    # Root cause patterns
    rc_match = re.search(
        r"(?:root\s*cause|problem|issue)\s*(?:was|is|:)\s*(.+?)(?:\n\n|\n(?=[A-Z#*]))",
        tail,
        re.IGNORECASE | re.DOTALL,
    )
    if rc_match:
        summary.root_cause = rc_match.group(1).strip()[:300]

    # File change patterns ("**`file.scala`** — description" or "- `file`: description")
    for m in re.finditer(
        r"(?:\*\*`?|[`*])([^`*\n]+\.(?:scala|java))`?\*?\*?\s*[-–—:]\s*(.+)",
        tail,
    ):
        summary.changes.append(f"`{m.group(1).strip()}`: {m.group(2).strip()[:200]}")

    # Test result patterns
    t_match = re.search(
        r"(?:tests?|suites?)\s*(?:result|status|:)\s*(.+?)(?:\n|$)",
        tail,
        re.IGNORECASE,
    )
    if t_match:
        summary.tests = t_match.group(1).strip()[:200]

    return summary


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

    _structured: StructuredSummary | None = field(default=None, repr=False)

    @property
    def structured_summary(self) -> StructuredSummary:
        if self._structured is None:
            self._structured = _parse_structured_summary(_strip_ansi(self.output))
        return self._structured

    @property
    def summary(self) -> str:
        s = self.structured_summary
        if s.found:
            return s.for_cliq()
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
      - Profile management (saturn-zdpas profile with MCP tools auto-loaded)
      - Pre-flight context scan (project structure + DPAAS env check)
      - Session instructions file (ZDPAS coding protocol injected at start)
      - Mandatory MCP tool usage (compile_quick + run_module_tests required)
      - Customised Goose tool set via --profile saturn-zdpas

    Tool advantage provided to Goose via Saturn MCP (loaded automatically):
      compile_quick      — Tier 1: fast incremental compile (5–30 s)
      compile_module     — Tier 1+: compile whole module
      run_module_tests   — Tier 2: targeted ScalaTest run (2–10 min)
      find_similar_code  — Pattern discovery: find existing implementations
      get_test_template  — Test scaffold: copy & adapt an existing test
      sync_resources     — Resource file visibility confirmation
      get_project_info   — Project structure overview
      get_module_context — Module-level context (files, classes, suites)
      search_code        — Fast grep across Scala/Java sources
      get_changed_files  — Which files the agent already modified
      get_dpaas_env      — DPAAS environment status

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

        # ZDPAS tools for context injection (Saturn-side; also exposed via MCP)
        self._tools = SaturnZDPASTools(workspace)

        # Ensure saturn-zdpas profile exists and MCP server is registered.
        # _setup_profile() returns the active profile name to pass to Goose.
        self._profile = self._setup_profile()

        # Cache: project structure (expensive to recompute each call)
        self._project_structure: str | None = None
        # Path to static context file for GOOSE_MOIM_MESSAGE_FILE
        self._context_file: str | None = None

    # ── Pre-flight context scan ───────────────────────────────────

    def pre_flight(self) -> str:
        """
        Gather project context before the main coding task begins.

        Saturn calls this automatically before GooseAgent.run() to:
          1. Confirm DPAAS environment is ready (jars, HOME path)
          2. Capture the project structure overview (cached for the session)
          3. Write static context to a file for Goose's "Top of Mind" extension
          4. Identify any environment issues early (fast-fail)

        Returns a summary string for logging; the gathered context is cached
        in self._project_structure for reuse inside run().
        """
        lines = ["  🔍 Pre-flight context scan..."]

        # 1. Cache project structure
        try:
            self._project_structure = self._tools.get_project_structure()
            module_count = self._project_structure.count("  ") // 2
            lines.append(f"  ✅ Project structure loaded ({module_count} modules)")
        except Exception as e:
            lines.append(f"  ⚠️  Could not load project structure: {e}")
            self._project_structure = "(unavailable)"

        # 2. Check DPAAS environment
        dpaas_home = (
            os.environ.get("DPAAS_HOME", "").strip() or settings.saturn_dpaas_home.strip()
        )
        if dpaas_home:
            jars_dir = Path(dpaas_home) / "zdpas" / "spark" / "jars"
            if jars_dir.exists() and list(jars_dir.glob("*.jar")):
                lines.append(f"  ✅ DPAAS_HOME ready: {dpaas_home}")
            else:
                lines.append(
                    f"  ⚠️  DPAAS jars not found at {jars_dir} — "
                    "compile_quick will fail until the 'setup' gate runs"
                )
        else:
            lines.append(
                "  ⚠️  DPAAS_HOME is not set — compile_quick will fail.\n"
                "     Set DPAAS_HOME in the runner VM shell profile or add\n"
                "     SATURN_DPAAS_HOME=/opt/dpaas to saturn.env"
            )

        # 3. Write static context file for Goose "Top of Mind" extension.
        #    This is loaded once into Goose's context instead of being
        #    stuffed into every --text prompt.
        self._context_file = self._write_static_context()
        if self._context_file:
            lines.append(f"  ✅ Static context written for Goose TOM")

        return "\n".join(lines)

    def _write_static_context(self) -> str | None:
        """Write static project context to a file for GOOSE_MOIM_MESSAGE_FILE."""
        try:
            ctx_path = Path(self.workspace) / ".saturn_context.md"
            sections = []

            if self._project_structure:
                sections.append(f"# ZDPAS Project Structure\n\n{self._project_structure}")

            # CLI tool commands — callable via Shell since cursor-agent
            # provider doesn't expose MCP extension tools directly.
            py = f"{Path(sys.executable)}"
            sections.append(
                "# Saturn ZDPAS Tools (call via Shell)\n\n"
                "These tools are available as CLI commands. Run them via Shell.\n\n"
                "## Discovery (use FIRST before writing any code)\n"
                f"```\n"
                f"{py} -m mcp.server --workspace . --call find_similar_code pattern=<keyword> module=<module>\n"
                f"{py} -m mcp.server --workspace . --call get_test_template module=<module> suite=<suite>\n"
                f"{py} -m mcp.server --workspace . --call search_code pattern=<pattern>\n"
                f"{py} -m mcp.server --workspace . --call get_module_context module=<module>\n"
                f"{py} -m mcp.server --workspace . --call get_project_info\n"
                f"```\n\n"
                "## Validation (REQUIRED after every edit)\n"
                f"```\n"
                f'{py} -m mcp.server --workspace . --call compile_quick \'{{"files":["path/to/file.scala"]}}\'\n'
                f"{py} -m mcp.server --workspace . --call compile_module module=<module>\n"
                f"{py} -m mcp.server --workspace . --call run_module_tests module=<module> suite=<suite>\n"
                f"```\n\n"
                "## Other\n"
                f"```\n"
                f"{py} -m mcp.server --workspace . --call sync_resources\n"
                f"{py} -m mcp.server --workspace . --call get_changed_files\n"
                f"{py} -m mcp.server --workspace . --call get_dpaas_env\n"
                f"```\n"
            )

            sections.append(
                "# Coding Workflow\n\n"
                "1. Discover: find_similar_code + get_module_context\n"
                "2. Edit: read file → make minimal change → compile_quick → fix errors\n"
                "3. Validate: compile_module → run_module_tests → confirm pass\n"
                "4. Only stop when tests pass\n\n"
                "# Rules\n"
                "- Use the Saturn CLI tools above for compile/test — do NOT run scalac/ant/sbt\n"
                "- NEVER commit or push — Saturn handles git automatically\n"
                "- NEVER modify tests to hide failures — fix the source code\n"
                "- Max 200 chars per line, 50 lines per method\n"
            )

            ctx_path.write_text("\n".join(sections), encoding="utf-8")
            return str(ctx_path)
        except Exception as e:
            print(f"  ⚠️  Could not write static context: {e}")
            return None

    # ── MCP quick-compile: Tier 1 feedback ───────────────────────

    def quick_compile(self, files: list[str]) -> str:
        """
        Tier 1: Fast incremental compilation of specific files.

        Called directly by Saturn (not Goose) when we want to validate
        the agent's changes without waiting for the full gate pipeline.
        Goose also calls this via the MCP server during its coding loop.

        Returns a human-readable result string.
        """
        from mcp.quick_compile import QuickCompiler
        import os
        dpaas_home = (
            os.environ.get("DPAAS_HOME", "").strip() or settings.saturn_dpaas_home.strip()
        )
        compiler = QuickCompiler(
            workspace=self.workspace,
            dpaas_home=dpaas_home,
        )
        result = compiler.compile(files)
        return result.format_errors()

    def run(
        self,
        task: str,
        files_changed: list[str] | None = None,
        timeout: int | None = None,
    ) -> GooseAgentResult:
        """
        Run a coding task via Goose with rich ZDPAS context and MCP tools.

        Three-tier feedback loop (Layer 6):
            Tier 1 — Static Validation: Goose calls compile_quick() after
                      every edit.  Fast (5–30 s).  Catches type errors and
                      syntax problems before moving on.
            Tier 2 — Unit Tests: Goose calls run_module_tests() before
                      finishing.  Mandatory — Goose must not stop until
                      tests pass for the affected module.
            Tier 3 — Full gate pipeline: Saturn runs after Goose finishes.
                      Tiers 1 & 2 have already caught most issues, so
                      gate retries are rare.

        Goose has access to these Saturn MCP tools (via saturn-zdpas profile):
            compile_quick      — Tier 1 fast compile
            run_module_tests   — Tier 2 unit/integration tests
            find_similar_code  — discover existing patterns before writing
            get_test_template  — get a copy-and-adapt test scaffold
            sync_resources     — confirm resource files are visible
            search_code        — grep across all Scala/Java sources
            get_module_context — module file list and key classes
            get_changed_files  — track what the agent already modified

        Args:
            task: Natural language task description
            files_changed: Files changed so far (for context injection)
            timeout: Override timeout in seconds

        Returns:
            GooseAgentResult with output, changed files, and session name
        """
        print(f"\n  🪿  GooseAgent.run() — session: {self.session_name}")
        print(f"  🔧 Profile: {self._profile or '(default)'}")

        # Build context-enriched prompt (includes mandatory MCP tool usage)
        prompt = self._build_rich_prompt(task, files_changed or [])

        # Snapshot files to detect changes
        files_before = self._cli._snapshot_files(self.workspace)

        # Run Goose with the Saturn profile (loads MCP tools automatically)
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
        Remove the Goose session and scratch files after the task completes.
        """
        session_dir = Path.home() / ".config" / "goose" / "sessions"
        if session_dir.exists():
            for f in session_dir.glob(f"{self.session_name}*"):
                try:
                    f.unlink()
                except Exception:
                    pass

        if self._context_file:
            try:
                Path(self._context_file).unlink(missing_ok=True)
            except Exception:
                pass

    # ── Private: prompt builders ──────────────────────────────────

    def _build_rich_prompt(self, task: str, files_changed: list[str]) -> str:
        """
        Build a lean task prompt.  Static context (project structure, MCP tool
        docs, workflow rules) is delivered via GOOSE_MOIM_MESSAGE_FILE so it is
        loaded once into Goose's context.  This prompt only carries dynamic,
        per-task information.
        """
        sections = [f"# Task\n\n{task}\n"]

        if files_changed:
            ctx = self._tools.get_changed_file_context(files_changed)
            sections.append(f"# Changed Files\n\n{ctx}\n")

        sections.append(
            "# Instructions\n\n"
            "Use your saturn-zdpas MCP tools for ALL compile/test operations.\n"
            "Workflow: discover → edit → compile_quick → fix → run_module_tests → confirm pass.\n"
            "Do NOT stop until tests pass.  Saturn handles git + MR automatically.\n"
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

    # How long Goose can be completely silent before we consider it stuck.
    IDLE_TIMEOUT = 120  # seconds

    def _stream_goose(
        self,
        prompt: str,
        timeout: int | None = None,
    ) -> tuple[list[str], int]:
        """
        Run Goose with a named session and stream output line-by-line.

        Uses subprocess.Popen instead of subprocess.run so we can read
        output as it arrives and show real-time progress.

        Timeout behaviour (activity-aware):
          - The hard wall-clock timeout (default 900 s) is the absolute cap.
          - While Goose is actively producing output it is considered "working".
          - We only kill the process when BOTH conditions are true:
              1. Total elapsed time > timeout
              2. Goose has been silent for > IDLE_TIMEOUT (120 s)
          - If the hard timeout is exceeded but Goose is still active, we
            allow an extra IDLE_TIMEOUT grace period for it to finish its
            current operation (e.g. a running scalac / ScalaTest).

        Returns: (output_lines, exit_code)
        """
        timeout = timeout or self.timeout
        env = self._build_env()

        if self._context_file and os.path.isfile(self._context_file):
            env["GOOSE_MOIM_MESSAGE_FILE"] = self._context_file

        system = (
            "You are an autonomous coding agent. Be concise: "
            "act first, explain only on failure. "
            "Use MCP tools (compile_quick, run_module_tests) — never shell for compile/test. "
            "One tool call per response when possible. "
            "Do not restate the task or plan at length — just execute.\n\n"
            "IMPORTANT: When you finish, output EXACTLY this block:\n"
            "```\n"
            "SATURN_SUMMARY\n"
            "ROOT_CAUSE: <1-2 sentence root cause>\n"
            "CHANGES:\n"
            "- <file>: <what changed and why>\n"
            "TESTS: <pass/fail + which suites ran>\n"
            "```"
        )

        if self._profile:
            cmd = [
                self._cli.goose_path,
                "run",
                "--profile", self._profile,
                "--session", self.session_name,
                "--system", system,
                "--max-turns", "30",
                "--text", prompt,
            ]
        else:
            cmd = [
                self._cli.goose_path,
                "run",
                "--system", system,
                "--max-turns", "30",
                "--text", prompt,
                "--with-builtin", "developer",
            ]

        print(f"     cmd: {' '.join(cmd[:6])} ... [prompt={len(prompt)} chars]")

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
                start_new_session=True,
            )

            assert process.stdout is not None

            if self.stream:
                line_q: queue.Queue[str | None] = queue.Queue()

                def _reader() -> None:
                    assert process.stdout is not None
                    for ln in iter(process.stdout.readline, ""):
                        line_q.put(ln)
                    line_q.put(None)

                t = threading.Thread(target=_reader, daemon=True)
                t.start()

                last_activity = time.time()
                grace_warned = False

                while True:
                    now = time.time()
                    elapsed = now - start_time
                    idle_secs = now - last_activity

                    if elapsed > timeout:
                        if idle_secs > self.IDLE_TIMEOUT:
                            self._kill_process_group(process)
                            output_lines.append(
                                f"\n⚠️  Goose timed out after {elapsed:.0f}s "
                                f"(idle for {idle_secs:.0f}s)"
                            )
                            return output_lines, -1
                        elif not grace_warned:
                            grace_warned = True
                            print(
                                f"\n  ⏳ Wall timeout ({timeout}s) reached but Goose "
                                f"is still active — granting {self.IDLE_TIMEOUT}s grace period..."
                            )

                    try:
                        line = line_q.get(timeout=5)
                    except queue.Empty:
                        continue

                    if line is None:
                        break

                    last_activity = time.time()
                    clean = _strip_ansi(line)
                    output_lines.append(clean.rstrip())
                    print(f"  🪿  {clean}", end="", flush=True)
            else:
                try:
                    stdout, _ = process.communicate(timeout=timeout)
                    output_lines = _strip_ansi(stdout).splitlines()
                except subprocess.TimeoutExpired:
                    self._kill_process_group(process)
                    process.wait()
                    output_lines.append(f"\n⚠️  Goose timed out after {timeout}s")
                    return output_lines, -1

            process.wait()
            return output_lines, process.returncode

        except FileNotFoundError:
            return [f"❌ Goose binary not found: {self._cli.goose_path}"], -1
        except Exception as e:
            return [f"❌ Goose error: {e}"], -1

    @staticmethod
    def _kill_process_group(process: subprocess.Popen) -> None:
        """Kill the entire process group so child processes don't linger."""
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    def _build_env(self) -> dict[str, str]:
        """Build environment for Goose subprocess."""
        return self._cli._build_env()

    def _setup_profile(self) -> str:
        """
        Set up the Saturn Goose profile and register the Saturn MCP server.

        Returns the profile name to pass to ``goose run --profile``.
        Returns an empty string when setup fails (Goose still works via
        --with-builtin developer, just without the Saturn MCP tools).
        """
        try:
            from agent.goose_profile import ensure_saturn_profile
            profile = ensure_saturn_profile(workspace=self.workspace)
            return profile
        except Exception as e:
            print(f"  ⚠️  Could not setup Goose profile/MCP: {e} — using defaults")
            return ""
