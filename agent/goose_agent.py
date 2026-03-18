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
import pty
import queue
import signal
import subprocess
import threading
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

    # ── Pre-flight context scan ───────────────────────────────────

    def pre_flight(self) -> str:
        """
        Gather project context before the main coding task begins.

        Saturn calls this automatically before GooseAgent.run() to:
          1. Confirm DPAAS environment is ready (jars, HOME path)
          2. Capture the project structure overview (cached for the session)
          3. Identify any environment issues early (fast-fail)

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
        import os
        dpaas_home = (
            os.environ.get("DPAAS_HOME", "").strip() or settings.saturn_dpaas_home.strip()
        )
        from pathlib import Path as _Path
        if dpaas_home:
            jars_dir = _Path(dpaas_home) / "zdpas" / "spark" / "jars"
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

        return "\n".join(lines)

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

        This prompt drives Goose's entire coding session.  It contains:
          1. The task description
          2. ZDPAS project structure (cached after first call)
          3. Changed files context (which modules are affected)
          4. Saturn MCP tool catalogue with REQUIRED usage protocol
          5. Explicit three-tier coding workflow
          6. Hard rules (no commit/push; always validate before finishing)
        """
        sections = [f"# Task\n\n{task}\n"]

        # Project structure (cached from pre_flight or computed now)
        if self._project_structure is None:
            self._project_structure = self._tools.get_project_structure()
        sections.append(f"# Project Structure\n\n{self._project_structure}\n")

        # Changed files context
        if files_changed:
            ctx = self._tools.get_changed_file_context(files_changed)
            sections.append(f"# Changed Files\n\n{ctx}\n")

        # Saturn MCP tool catalogue — available via saturn-zdpas profile
        sections.append(
            "# Saturn MCP Tools (via saturn-zdpas extension)\n\n"
            "These tools are loaded automatically through your Goose profile.\n"
            "Use them at every step — they save significant time.\n\n"
            "## Discovery tools (use FIRST before writing any code)\n"
            "**find_similar_code(pattern, module=\"\")**\n"
            "  Find existing implementations similar to what you need to write.\n"
            "  Always call this first — follow the existing patterns for consistency.\n"
            "  Example: find_similar_code(\"ZDFilter\", module=\"transformer\")\n\n"
            "**get_test_template(module, suite=\"\")**\n"
            "  Get a copy-and-adapt test scaffold from an existing suite.\n"
            "  Always call this before adding new test cases.\n"
            "  Example: get_test_template(\"transformer\", suite=\"ZDTrimSuite\")\n\n"
            "**search_code(pattern)**\n"
            "  Fast grep across all Scala/Java sources.\n"
            "  Example: search_code(\"applyTransformation\")\n\n"
            "**get_module_context(module)**\n"
            "  Get source files, test suites, and key classes for a module.\n\n"
            "**get_project_info()**\n"
            "  ZDPAS project structure overview — all modules + file counts.\n\n"
            "## Validation tools (use AFTER every edit)\n"
            "**compile_quick(files)**\n"
            "  REQUIRED after each file edit. Fast Tier-1 compile check (5–30 s).\n"
            "  Fix ALL errors before moving to the next file.\n"
            "  Example: compile_quick([\"source/com/zoho/dpaas/transformer/ZDFilter.scala\"])\n\n"
            "**compile_module(module)**\n"
            "  Compile all sources in a module. Run before run_module_tests.\n"
            "  Example: compile_module(\"transformer\")\n\n"
            "**run_module_tests(module, suite=\"\")**\n"
            "  REQUIRED before finishing. Run Tier-2 unit tests for the module.\n"
            "  You MUST call this and confirm tests pass before stopping.\n"
            "  Example: run_module_tests(\"transformer\", suite=\"ZDTrimSuite\")\n\n"
            "## Resource & environment tools\n"
            "**sync_resources()**\n"
            "  Call after adding any resource files (CSV, JSON, XML, etc.).\n"
            "  Confirms they are on the test classpath.\n\n"
            "**get_changed_files()**\n"
            "  See which files you have already modified.\n\n"
            "**get_dpaas_env()**\n"
            "  Check DPAAS_HOME status and jar availability.\n"
        )

        sections.append(
            "# Required Coding Workflow (FOLLOW THIS EXACTLY)\n\n"
            "## Step 1 — Discover before writing\n"
            "1a. Call find_similar_code(\"<keyword>\") to find existing patterns\n"
            "1b. Call get_module_context(\"<module>\") to understand the module\n"
            "1c. If adding tests: call get_test_template(\"<module>\") for the scaffold\n\n"
            "## Step 2 — Edit code\n"
            "2a. Read the target file first\n"
            "2b. Make the minimal focused change\n"
            "2c. IMMEDIATELY call compile_quick([\"path/to/changed/file.scala\"])\n"
            "2d. Fix ALL compile errors before editing any other file\n"
            "2e. Repeat 2a–2d for each file\n\n"
            "## Step 3 — Validate (MANDATORY before stopping)\n"
            "3a. Call compile_module(\"<module>\") on the affected module\n"
            "3b. Call run_module_tests(\"<module>\") — confirm tests pass\n"
            "3b. If you added tests: read the test failure, fix the SOURCE code (not tests), repeat from 3a\n"
            "3d. If tests fail: read the failure, fix the source, repeat from 2c\n"
            "3e. If you added resource files: call sync_resources()\n\n"
            "3f. Only stop when all tests pass for the affected module\n\n"
            "3g. This ZDPAS Project don't have any predefined auto discoverable libs to compile read gates/config.py -> "
            "_get_zdpas_gates and run affected module suites if needed always use _get_zdpas_gates not gitlab-ci in dpaas repo to know which suites to run for a module\n\n"
            "## Repo Rules\n"
            "- Follow existing code patterns — use find_similar_code() and get_test_template() before writing new code or tests\n"
            "- Before commit must follow these\n"
            "1a. A line should not have more than 200 chars\n"
            "1b. A method should not go beyond 50 lines\n"
            "1c. Formatting\n"
            "1d. Resolve warnings (MUST)\n"
            "1e. Documentations\n"
            "## Hard rules\n"
            "- NEVER stop without calling run_module_tests() and confirming pass. your are an Autonomous Coding Agent we provided MCPs which are preloaded in Goose : ~/.config/goose/config.yaml if needed look into validate_gates.sh\n"
            "- Echo unit case result if ran.\n"
            "- NEVER commit, push, or run scalac/ant/sbt manually\n"
            "- NEVER modify tests to hide failures — fix the source code\n"
            "- Saturn handles git commit + push + MR creation automatically\n"
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

        # Build command: use Saturn profile when available (loads MCP tools).
        # Fall back to --with-builtin developer only when profile is absent.
        if self._profile:
            cmd = [
                self._cli.goose_path,
                "run",
                "--profile", self._profile,
                "--session", self.session_name,
                "--text", prompt,
            ]
        else:
            cmd = [
                self._cli.goose_path,
                "run",
                "--text", prompt,
                "--with-builtin", "developer",
            ]

        print(f"     cmd: {' '.join(cmd[:6])} ... [prompt={len(prompt)} chars]")

        output_lines: list[str] = []
        start_time = time.time()

        try:
            # Use a pty for stdout so Goose (Rust) uses line buffering
            # instead of full buffering (Rust detects the TTY and flushes
            # after each line, giving us real-time streaming).
            primary_fd, replica_fd = pty.openpty()

            process = subprocess.Popen(
                cmd,
                cwd=self.workspace,
                stdout=replica_fd,
                stderr=replica_fd,
                stdin=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
            os.close(replica_fd)

            primary_file = os.fdopen(primary_fd, "r", errors="replace")

            if self.stream:
                line_q: queue.Queue[str | None] = queue.Queue()

                def _reader() -> None:
                    try:
                        for ln in iter(primary_file.readline, ""):
                            line_q.put(ln)
                    except OSError:
                        pass
                    line_q.put(None)

                t = threading.Thread(target=_reader, daemon=True)
                t.start()

                while True:
                    elapsed = time.time() - start_time
                    if elapsed > timeout:
                        self._kill_process_group(process)
                        output_lines.append(
                            f"\n⚠️  Goose timed out after {timeout}s"
                        )
                        return output_lines, -1

                    try:
                        line = line_q.get(timeout=5)
                    except queue.Empty:
                        continue

                    if line is None:
                        break

                    clean = _strip_ansi(line)
                    output_lines.append(clean.rstrip())
                    print(f"  🪿  {clean}", end="", flush=True)
            else:
                try:
                    process.wait(timeout=timeout)
                    output = primary_file.read()
                    output_lines = _strip_ansi(output).splitlines()
                except subprocess.TimeoutExpired:
                    self._kill_process_group(process)
                    process.wait()
                    output_lines.append(f"\n⚠️  Goose timed out after {timeout}s")
                    return output_lines, -1

            primary_file.close()
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
