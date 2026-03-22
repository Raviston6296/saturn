"""
Saturn MCP Server — ZDPAS Toolshed.

Provides fast, structured ZDPAS development tools to AI agents (Goose, Cursor,
Claude Desktop, etc.) via the Model Context Protocol (MCP).

This is Layer 2 of the Stripe Minions architecture: Context Hydration with MCP.

Tools exposed:
    compile_quick(files)          → fast incremental compile (Tier 1)
    compile_module(module)        → compile whole module (Tier 1+)
    run_module_tests(module, ...)  → targeted ScalaTest run (Tier 2)
    get_project_info()            → ZDPAS structure overview
    search_code(pattern, ...)     → grep across Scala sources
    get_module_context(module)    → source files, test suites, key classes
    get_changed_files()           → git diff --name-only

Starting the server
-------------------
As a stdio MCP server (default — for Goose/Cursor integration):
    python -m mcp.server

As a background server started by Saturn:
    from mcp.server import SaturnMCPServer
    server = SaturnMCPServer(workspace="/path/to/zdpas")
    server.start_background()

Goose integration (auto-configured by agent/goose_profile.py):
    ~/.config/goose/config.yaml:
        extensions:
          saturn-zdpas:
            type: stdio
            cmd: python
            args: [-m, mcp.server]
            env_keys: [DPAAS_HOME, DPAAS_SOURCE_TAR, BUILD_FILE_HOME]
            timeout: 120
            description: "Saturn ZDPAS tools — quick compile, tests, code search"
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


# ── Tool implementations ──────────────────────────────────────────


class SaturnMCPTools:
    """
    All tool implementations used by the MCP server.

    Kept separate from the server wiring so they can be unit-tested
    independently and reused from GooseAgent for context injection.
    """

    def __init__(self, workspace: str = ".", dpaas_home: str = ""):
        self.workspace = Path(workspace).resolve()
        self.dpaas_home = Path(
            dpaas_home
            or os.environ.get("DPAAS_HOME", "")
            or "/opt/dpaas"
        )

    # ── Tier 1: Quick compile ─────────────────────────────────────

    def compile_quick(self, files: list[str]) -> str:
        """
        Fast incremental Scala/Java compilation check.

        Compiles only the specified files (not the full source tree).
        Returns errors immediately — no JAR produced.
        Typical speed: 5–30 s for 1–5 changed files.

        Use this during your coding loop for immediate feedback.
        The full gate pipeline (compile all → test) will run after
        you finish — this is just a quick sanity check.

        Args:
            files: List of source file paths relative to workspace
                   e.g. ["source/com/zoho/dpaas/transformer/ZDFilter.scala"]
        """
        from mcp.quick_compile import QuickCompiler
        compiler = QuickCompiler(
            workspace=str(self.workspace),
            dpaas_home=str(self.dpaas_home),
        )
        result = compiler.compile(files)
        return result.format_errors()

    def compile_module(self, module: str) -> str:
        """
        Compile all sources in a ZDPAS module.

        Faster than the full gate compile (one module only).
        Use before run_module_tests to catch compile errors first.

        Args:
            module: ZDPAS module name, e.g. "transformer", "util", "dataframe"
        """
        from mcp.quick_compile import QuickCompiler
        compiler = QuickCompiler(
            workspace=str(self.workspace),
            dpaas_home=str(self.dpaas_home),
        )
        result = compiler.compile_module(module)
        return result.format_errors()

    # ── Tier 2: Module tests ──────────────────────────────────────

    def run_module_tests(
        self,
        module: str,
        suite: str = "",
        timeout: int = 300,
    ) -> str:
        """
        Run ScalaTest for a ZDPAS module or specific suite.

        Requires dpaas.jar and dpaas_test.jar to already be built
        (from the gate pipeline or a previous compile call).

        Args:
            module: ZDPAS module name ("transformer", "util", etc.)
                    or fully-qualified package ("com.zoho.dpaas.transformer")
            suite:  Optional specific suite name ("ZDTrimSuite")
                    or empty string to run all suites in the module
            timeout: Seconds to wait (default 300)
        """
        dpaas_jar = self.workspace / "dpaas.jar"
        test_jar = self.workspace / "dpaas_test.jar"

        if not dpaas_jar.exists():
            return (
                "❌ dpaas.jar not found in workspace. "
                "Run the 'compile' gate first, or call compile_module() to build it."
            )
        if not test_jar.exists():
            return (
                "❌ dpaas_test.jar not found in workspace. "
                "Run the 'build-test-jar' gate first."
            )

        # Build -w / -s argument
        if suite:
            # Resolve suite to FQN
            fqn = self._resolve_suite(module, suite)
            test_arg = f"-s {fqn}"
        elif module.startswith("com.zoho."):
            test_arg = f"-w {module}"
        else:
            test_arg = f"-w com.zoho.dpaas.{module}"

        # Include workspace resource directories in classpath so test cases
        # can find resource files added by the agent (e.g. CSV/JSON fixtures
        # placed in resources/ or test/resources/ during the coding loop).
        classpath_parts = [
            str(self.workspace / "dpaas_test.jar"),
            str(self.workspace / "dpaas.jar"),
        ]
        for res_dir in ("test/resources", "resources"):
            res_path = self.workspace / res_dir
            if res_path.exists():
                classpath_parts.append(str(res_path))
        classpath_parts += [
            str(self.dpaas_home / "zdpas" / "spark" / "jars" / "*"),
            str(self.dpaas_home / "zdpas" / "spark" / "app_blue" / "ExpParser.jar"),
            str(self.dpaas_home / "zdpas" / "spark" / "lib" / "*"),
        ]
        classpath = ":".join(classpath_parts)

        # Pass DPAAS_HOME as a JVM system property (-D flag) so Scala/Java test
        # code can read it via System.getProperty("DPAAS_HOME") when running in
        # a separate JVM subprocess (shell mode).  The env var is also kept for
        # backward-compatibility with code still using sys.env("DPAAS_HOME").
        cmd = (
            f"java -cp \"{classpath}\" "
            f"-Xmx3g "
            f"-DDPAAS_HOME={self.dpaas_home} "
            f"-Dserver.dir={self.dpaas_home}/zdpas/spark "
            f"org.scalatest.tools.Runner "
            f"-R ./dpaas_test.jar "
            f"{test_arg} "
            f"-oC 2>&1"
        )

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=str(self.workspace),
                capture_output=False,
                text=True,
                timeout=timeout,
                env={**os.environ, "DPAAS_HOME": str(self.dpaas_home)},
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            output = result.stdout or ""
        except subprocess.TimeoutExpired:
            return f"⏱️  Tests timed out after {timeout}s"

        return self._format_test_output(output, result.returncode)

    # ── Context tools ─────────────────────────────────────────────

    def get_project_info(self) -> str:
        """
        Get an overview of the ZDPAS project structure.

        Returns module names, file counts, and test suite counts.
        Use this to orient yourself before searching for specific files.
        """
        from saturn_goose.toolkit import SaturnZDPASTools
        tools = SaturnZDPASTools(str(self.workspace))
        return tools.get_project_structure()

    def get_module_context(self, module: str) -> str:
        """
        Get detailed context about a specific ZDPAS module.

        Returns source files, test suites, key class names, and a description
        of what the module does.

        Args:
            module: Module name e.g. "transformer", "dataframe", "util"
        """
        from saturn_goose.toolkit import SaturnZDPASTools
        tools = SaturnZDPASTools(str(self.workspace))
        return tools.get_module_context(module)

    def search_code(
        self,
        pattern: str,
        file_glob: str = "*.scala",
        include_tests: bool = True,
        max_results: int = 20,
    ) -> str:
        """
        Search for a pattern across ZDPAS source files.

        Faster than listing directories and reading files one by one.

        Args:
            pattern:      Text or regex to search for
            file_glob:    File filter (default: *.scala)
            include_tests: Also search test/source/ (default: True)
            max_results:  Maximum matches to return (default: 20)
        """
        from saturn_goose.toolkit import SaturnZDPASTools
        tools = SaturnZDPASTools(str(self.workspace))
        return tools.search_scala_files(
            pattern=pattern,
            file_glob=file_glob,
            include_tests=include_tests,
            max_results=max_results,
        )

    def get_changed_files(self, base_ref: str = "HEAD") -> str:
        """
        Get the list of files changed vs a base git ref.

        Useful to know which files the agent has already modified.

        Args:
            base_ref: Git ref to diff against (default: HEAD = uncommitted changes)
        """
        try:
            result = subprocess.run(
                f"git diff --name-only {base_ref}",
                shell=True,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=15,
            )
            untracked = subprocess.run(
                "git ls-files --others --exclude-standard",
                shell=True,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=15,
            )
            files = sorted(set(
                (result.stdout + untracked.stdout).strip().splitlines()
            ))
            if not files:
                return "No changed files (working tree is clean vs HEAD)."

            from saturn_goose.toolkit import SaturnZDPASTools
            tools = SaturnZDPASTools(str(self.workspace))
            return tools.get_changed_file_context(files)

        except Exception as e:
            return f"Could not get changed files: {e}"

    def find_similar_code(
        self,
        pattern: str,
        module: str = "",
        max_results: int = 5,
    ) -> str:
        """
        Find similar existing implementations to use as coding patterns.

        Before writing new code, call this to see how similar functionality
        is already implemented in the codebase.  Goose should follow these
        existing patterns for consistency.

        Args:
            pattern:     Class name, method name, or keyword to find
                         e.g. "ZDFilter", "applyTransformation", "readCsv"
            module:      Limit search to one module (optional)
                         e.g. "transformer", "dataframe"
            max_results: Maximum number of examples to return (default 5)

        Returns:
            Formatted list of similar implementations with file + snippet
        """
        from saturn_goose.toolkit import SaturnZDPASTools
        tools = SaturnZDPASTools(str(self.workspace))

        search_dirs = []
        if module:
            for sub in ("source", "test/source"):
                p = self.workspace / sub / "com" / "zoho" / "dpaas" / module
                if p.exists():
                    search_dirs.append(str(p.relative_to(self.workspace)))
        # If no module or nothing found, search everywhere
        if not search_dirs:
            search_dirs = ["source", "test/source"]

        lines = [f"## Similar Implementations for '{pattern}'\n"]
        found = 0

        for search_dir in search_dirs:
            dir_path = self.workspace / search_dir
            if not dir_path.exists():
                continue
            try:
                result = subprocess.run(
                    ["grep", "-rn", "--include=*.scala", "--include=*.java",
                     "-l", pattern, str(dir_path)],
                    capture_output=True, text=True, timeout=15,
                    cwd=str(self.workspace),
                )
                for file_path in result.stdout.strip().splitlines():
                    if found >= max_results:
                        break
                    try:
                        rel = str(Path(file_path).relative_to(self.workspace))
                        content = Path(file_path).read_text(encoding="utf-8", errors="ignore")
                        # Extract the surrounding class/method context
                        snippet_lines = []
                        for i, line in enumerate(content.splitlines()):
                            if pattern in line:
                                start = max(0, i - 3)
                                end = min(len(content.splitlines()), i + 8)
                                snippet_lines = content.splitlines()[start:end]
                                break
                        lines.append(f"### {rel}")
                        lines.append("```scala")
                        lines.extend(snippet_lines[:12])
                        lines.append("```\n")
                        found += 1
                    except Exception:
                        continue
            except Exception:
                continue

        if found == 0:
            lines.append(f"No existing implementations found for '{pattern}'.")
            lines.append("You may be implementing this for the first time.")

        return "\n".join(lines)

    def get_test_template(
        self,
        module: str,
        suite: str = "",
    ) -> str:
        """
        Get a minimal test template extracted from an existing test suite.

        Use this when adding a new test case so you follow the exact same
        structure, fixture usage, and assertions as existing tests in the
        module.  Copy the template and adapt it for your new test.

        Args:
            module:  ZDPAS module name e.g. "transformer", "util"
            suite:   Optional specific suite name e.g. "ZDTrimSuite"
                     If empty, picks the first suite found in the module

        Returns:
            A test template extracted from an existing suite file,
            showing the class structure, fixtures, and a sample test case
        """
        test_root = (
            self.workspace / "test" / "source" / "com" / "zoho" / "dpaas" / module
        )
        if not test_root.exists():
            return f"No test directory found for module '{module}'."

        # Find the target suite file
        suite_file: Path | None = None
        if suite:
            for f in test_root.rglob(f"{suite}.scala"):
                suite_file = f
                break
        if suite_file is None:
            # Pick first suite found
            for f in test_root.rglob("*Suite.scala"):
                suite_file = f
                break

        if suite_file is None:
            return f"No test suite files found in module '{module}'."

        try:
            content = suite_file.read_text(encoding="utf-8", errors="ignore")
            rel = str(suite_file.relative_to(self.workspace))
        except Exception as e:
            return f"Could not read suite file: {e}"

        lines_all = content.splitlines()
        total = len(lines_all)

        # Extract: package + imports + class declaration + first test + closing brace
        template_lines = []
        in_first_test = False
        test_count = 0
        brace_depth = 0

        for i, line in enumerate(lines_all):
            stripped = line.strip()
            # Always include package, import, and class header
            if stripped.startswith("package ") or stripped.startswith("import "):
                template_lines.append(line)
                continue
            if stripped.startswith("class ") or stripped.startswith("object "):
                template_lines.append(line)
                brace_depth += line.count("{") - line.count("}")
                continue

            # Track brace depth
            if template_lines:
                brace_depth += line.count("{") - line.count("}")

            # Include first test case only
            if 'test("' in line or "test(\"" in line:
                test_count += 1
                if test_count == 1:
                    in_first_test = True

            if in_first_test:
                template_lines.append(line)
                if brace_depth <= 1 and test_count > 0 and i > 5:
                    in_first_test = False
                    template_lines.append("  // ... add your test cases here ...")
                    template_lines.append("}")
                    break

        if not template_lines:
            # Fallback: return first 40 lines
            template_lines = lines_all[:40] + ["  // ... (truncated)"]

        return (
            f"## Test Template from {rel}\n\n"
            f"```scala\n"
            + "\n".join(template_lines)
            + "\n```\n\n"
            "Copy this template, rename the class and test cases, "
            "then adapt the assertions for your new test."
        )

    def get_dpaas_env(self) -> str:
        """
        Check the DPAAS runtime environment status.

        Shows DPAAS_HOME, whether jars are present, and which tars are
        available for the setup gate.
        """
        lines = ["## DPAAS Environment Status\n"]

        dpaas_home = os.environ.get("DPAAS_HOME", str(self.dpaas_home))
        lines.append(f"DPAAS_HOME: {dpaas_home}")

        jars_dir = Path(dpaas_home) / "zdpas" / "spark" / "jars"
        if jars_dir.exists():
            jar_count = len(list(jars_dir.glob("*.jar")))
            lines.append(f"Runtime jars: {jar_count} jars in {jars_dir}")
        else:
            lines.append("Runtime jars: ❌ Not found — run the 'setup' gate first")

        for tar_name, env_var, default in [
            ("dpaas.tar.gz", "DPAAS_SOURCE_TAR", "build/ZDPAS/output/dpaas.tar.gz"),
            ("dpaas_test.tar.gz", "DPAAS_TEST_TAR", "build/ZDPAS/output/dpaas_test.tar.gz"),
        ]:
            tar_path = Path(
                os.environ.get(env_var, "")
                or str(self.workspace / default)
            )
            status = "✅" if tar_path.exists() else "❌ not found"
            size = f" ({tar_path.stat().st_size // 1024 // 1024} MB)" if tar_path.exists() else ""
            lines.append(f"{tar_name}: {status}{size}  [{env_var}={tar_path}]")

        for jar_name in ["dpaas.jar", "dpaas_test.jar"]:
            jar = self.workspace / jar_name
            status = "✅" if jar.exists() else "❌ not built yet"
            lines.append(f"{jar_name}: {status}")

        return "\n".join(lines)

    def sync_resources(self) -> str:
        """
        Ensure all agent-added resource files are visible to the test runner.

        When the agent creates new resource files under resources/ or
        test/resources/, this tool reports their paths and confirms they
        will be included in the test classpath for the next test run.
        No file copying is needed — the workspace resource directories are
        already on the test classpath when run_module_tests() is called.

        Call this after adding any resource files (CSV, JSON, XML, etc.)
        and before running run_module_tests() to confirm visibility.
        """
        lines = ["## Resource Files Status\n"]

        for res_dir in ("resources", "test/resources"):
            res_path = self.workspace / res_dir
            if res_path.exists():
                files = sorted(
                    str(f.relative_to(self.workspace))
                    for f in res_path.rglob("*")
                    if f.is_file()
                )
                lines.append(f"### {res_dir}/ ({len(files)} files)")
                for f in files[:20]:
                    lines.append(f"  ✅ {f}")
                if len(files) > 20:
                    lines.append(f"  ... and {len(files) - 20} more")
                lines.append("")
            else:
                lines.append(f"### {res_dir}/ — not present")
                lines.append("")

        lines.append(
            "These directories are automatically included in the test classpath.\n"
            "Any file added here is immediately available to ScalaTest suites\n"
            "without rebuilding dpaas_test.jar."
        )
        return "\n".join(lines)

    # ── Helpers ───────────────────────────────────────────────────

    def _resolve_suite(self, module: str, suite: str) -> str:
        """Resolve a suite name to its fully-qualified class name."""
        if "." in suite:
            return suite  # already FQN

        # Search test directories
        for pkg in [module, "transformer", "dataframe", "storage", "util"]:
            candidate = (
                self.workspace
                / "test" / "source" / "com" / "zoho" / "dpaas"
                / pkg / f"{suite}.scala"
            )
            if candidate.exists():
                return f"com.zoho.dpaas.{pkg}.{suite}"

        # Default: assume transformer
        return f"com.zoho.dpaas.{module}.{suite}"

    def _format_test_output(self, output: str, exit_code: int) -> str:
        """Format ScalaTest output to show pass/fail counts clearly."""
        lines = output.splitlines()

        # Find summary line  (ScalaTest -oC output)
        summary_lines = [l for l in lines if "passed" in l or "failed" in l]
        failures = [l for l in lines if "*** FAILED ***" in l or "FAILED" in l]

        result_lines = []
        if exit_code == 0:
            result_lines.append("✅ Tests passed")
        else:
            result_lines.append(f"❌ Tests failed ({len(failures)} failure(s))")
            for f in failures[:10]:
                result_lines.append(f"  {f.strip()}")

        if summary_lines:
            result_lines.append("\nSummary:")
            for s in summary_lines[-3:]:
                result_lines.append(f"  {s.strip()}")

        return "\n".join(result_lines)


# ── MCP Server (stdio) ────────────────────────────────────────────


_TOOL_SCHEMAS = [
    {
        "name": "compile_quick",
        "description": (
            "Fast incremental Scala/Java compilation check (5–30 s). "
            "Compiles only the specified changed files — no JAR produced. "
            "Use this during your coding loop for immediate error feedback. "
            "The full gate pipeline will run after you finish."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Workspace-relative paths to .scala/.java files to compile",
                }
            },
            "required": ["files"],
        },
    },
    {
        "name": "compile_module",
        "description": (
            "Compile all sources in a ZDPAS module (faster than full gate compile). "
            "Use before run_module_tests to catch compile errors first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "description": "ZDPAS module name, e.g. 'transformer', 'util', 'dataframe'",
                }
            },
            "required": ["module"],
        },
    },
    {
        "name": "run_module_tests",
        "description": (
            "Run ScalaTest for a ZDPAS module or specific suite (Tier 2 feedback). "
            "Requires dpaas.jar and dpaas_test.jar to be built first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "description": "Module name ('transformer') or FQN ('com.zoho.dpaas.transformer')",
                },
                "suite": {
                    "type": "string",
                    "description": "Optional: specific suite name e.g. 'ZDTrimSuite'",
                    "default": "",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Seconds to wait (default 300)",
                    "default": 300,
                },
            },
            "required": ["module"],
        },
    },
    {
        "name": "get_project_info",
        "description": "Get ZDPAS project structure overview (modules, file counts, test suites).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_module_context",
        "description": "Get detailed context about a specific ZDPAS module: files, classes, tests.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "description": "Module name e.g. 'transformer'",
                }
            },
            "required": ["module"],
        },
    },
    {
        "name": "search_code",
        "description": "Search for a pattern across ZDPAS Scala/Java source files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Text or regex pattern to search for",
                },
                "file_glob": {
                    "type": "string",
                    "description": "File filter (default: *.scala)",
                    "default": "*.scala",
                },
                "include_tests": {
                    "type": "boolean",
                    "description": "Also search test/source/ (default: true)",
                    "default": True,
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum matches to return (default: 20)",
                    "default": 20,
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "get_changed_files",
        "description": "Get files changed vs HEAD and their affected ZDPAS modules.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "base_ref": {
                    "type": "string",
                    "description": "Git ref to diff against (default: HEAD)",
                    "default": "HEAD",
                }
            },
        },
    },
    {
        "name": "get_dpaas_env",
        "description": "Check DPAAS runtime environment: DPAAS_HOME, jars, available tars.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "sync_resources",
        "description": (
            "Confirm that agent-added resource files (CSV, JSON, XML, etc.) in "
            "resources/ and test/resources/ are visible to the test runner. "
            "These directories are already on the test classpath — no rebuild needed. "
            "Call this after adding resource files, before run_module_tests()."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "find_similar_code",
        "description": (
            "Find existing implementations similar to what you need to write. "
            "Returns real code snippets from the codebase to use as patterns. "
            "Always call this BEFORE writing new code to ensure consistency "
            "with existing style and conventions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Class name, method name, or keyword to search for",
                },
                "module": {
                    "type": "string",
                    "description": "Limit search to a specific module (optional)",
                    "default": "",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Max number of examples (default 5)",
                    "default": 5,
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "get_test_template",
        "description": (
            "Get a test template extracted from an existing suite in the module. "
            "Use this before adding new test cases — copy the template and adapt it. "
            "Ensures your tests follow the exact same structure as existing tests."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "description": "ZDPAS module name e.g. 'transformer', 'util'",
                },
                "suite": {
                    "type": "string",
                    "description": "Optional: specific suite name e.g. 'ZDTrimSuite'",
                    "default": "",
                },
            },
            "required": ["module"],
        },
    },
]


class SaturnMCPServer:
    """
    Saturn MCP stdio server.

    Speaks the MCP JSON-RPC protocol over stdin/stdout.
    Launched by Goose (or Cursor/Claude Desktop) as a subprocess.

    This is a self-contained implementation that does not require the
    `mcp` Python package — it implements the MCP stdio protocol directly.
    The protocol is simple JSON-RPC 2.0 with a few MCP-specific methods.
    """

    PROTOCOL_VERSION = "2025-03-26"

    def __init__(
        self,
        workspace: str = ".",
        dpaas_home: str = "",
        server_name: str = "saturn-zdpas",
    ):
        self.server_name = server_name
        self.tools = SaturnMCPTools(workspace=workspace, dpaas_home=dpaas_home)
        self._tool_map = {t["name"]: t for t in _TOOL_SCHEMAS}

    def run_stdio(self):
        """
        Run the MCP server in stdio mode (called by Goose as a subprocess).

        Reads JSON-RPC requests from stdin, writes responses to stdout.
        Auto-detects the transport format on the first message:
          - Content-Length framed (LSP-style, per MCP spec)
          - Newline-delimited JSON (used by Goose 1.x)
        """
        import traceback as _tb
        reader = sys.stdin.buffer
        writer = sys.stdout.buffer
        use_framing: bool | None = None

        while True:
            try:
                if use_framing is None:
                    message, use_framing = self._read_first_message(reader)
                elif use_framing:
                    message = self._read_framed(reader)
                else:
                    message = self._read_jsonline(reader)

                if message is None:
                    break
                request = json.loads(message)
            except (EOFError, KeyboardInterrupt):
                break
            except json.JSONDecodeError:
                continue
            except Exception:
                print(_tb.format_exc(), file=sys.stderr)
                break

            try:
                response = self._handle(request)
                if response is not None:
                    self._write_message(writer, response)
            except Exception:
                print(_tb.format_exc(), file=sys.stderr)

    @staticmethod
    def _read_first_message(reader) -> tuple[str | None, bool]:
        """Read the first message and detect transport format."""
        first_line = reader.readline()
        if not first_line:
            return None, False
        text = first_line.decode("utf-8").strip()
        if text.startswith("{"):
            return text, False
        if text.lower().startswith("content-length:"):
            cl = int(text.split(":", 1)[1].strip())
            while True:
                h = reader.readline().decode("utf-8").strip()
                if not h:
                    break
            body = reader.read(cl)
            return body.decode("utf-8") if body else None, True
        return None, False

    @staticmethod
    def _read_framed(reader) -> str | None:
        """Read a Content-Length framed message."""
        content_length = -1
        while True:
            header_line = reader.readline()
            if not header_line:
                return None
            header = header_line.decode("utf-8").strip()
            if not header:
                break
            if header.lower().startswith("content-length:"):
                content_length = int(header.split(":", 1)[1].strip())
        if content_length < 0:
            return None
        body = reader.read(content_length)
        return body.decode("utf-8") if body else None

    @staticmethod
    def _read_jsonline(reader) -> str | None:
        """Read a newline-delimited JSON message, skipping blank lines."""
        while True:
            line = reader.readline()
            if not line:
                return None
            text = line.decode("utf-8").strip()
            if text:
                return text

    @staticmethod
    def _write_message(writer, response: dict) -> None:
        """Write a JSON-line response (Goose 1.x uses raw JSON lines,
        not Content-Length framing, in both directions)."""
        body = json.dumps(response).encode("utf-8")
        writer.write(body + b"\n")
        writer.flush()

    def _handle(self, req: dict) -> dict | None:
        """Dispatch a JSON-RPC request to the appropriate MCP handler."""
        method = req.get("method", "")
        req_id = req.get("id")
        params = req.get("params", {})

        try:
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method in ("initialized", "notifications/initialized"):
                return None
            elif method == "tools/list":
                result = {"tools": _TOOL_SCHEMAS}
            elif method == "tools/call":
                result = self._handle_tool_call(params)
            elif method == "ping":
                result = {}
            elif req_id is None:
                return None  # unknown notification — no response
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}",
                    },
                }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": str(e)},
            }

        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def _handle_initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": self.server_name,
                "version": "1.0.0",
            },
        }

    def _handle_tool_call(self, params: dict) -> dict:
        """Execute a tool call and return MCP content response."""
        name = params.get("name", "")
        arguments = params.get("arguments", {})

        if name not in self._tool_map:
            raise ValueError(f"Unknown tool: {name}")

        tool_method = getattr(self.tools, name, None)
        if tool_method is None:
            raise ValueError(f"Tool not implemented: {name}")

        output = tool_method(**arguments)

        return {
            "content": [
                {"type": "text", "text": str(output)}
            ],
            "isError": False,
        }


# ── Entry point ───────────────────────────────────────────────────


def main():
    """Saturn MCP server — stdio mode or direct CLI tool calls."""
    import argparse
    parser = argparse.ArgumentParser(description="Saturn ZDPAS MCP Server")
    parser.add_argument(
        "--workspace",
        default=os.environ.get("SATURN_WORKSPACE", "."),
        help="Path to the ZDPAS workspace (default: .)",
    )
    parser.add_argument(
        "--dpaas-home",
        default=os.environ.get("DPAAS_HOME", ""),
        help="Path to DPAAS_HOME (default: from DPAAS_HOME env var)",
    )
    parser.add_argument(
        "--name",
        default=os.environ.get("SATURN_MCP_NAME", "saturn-zdpas"),
        help=(
            "MCP server id in initialize.serverInfo.name (default: saturn-zdpas). "
            "Match Goose extension name for clarity."
        ),
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="Print tool names (one per line) and exit — no stdio server.",
    )
    parser.add_argument(
        "--call",
        metavar="TOOL",
        help="Call a tool directly (skip MCP stdio). Args as positional JSON.",
    )
    parser.add_argument(
        "tool_args",
        nargs="*",
        help="JSON or key=value arguments for --call",
    )
    args = parser.parse_args()

    if args.list_tools:
        print(f"# MCP server name: {args.name}")
        for t in _TOOL_SCHEMAS:
            print(t["name"])
        return

    if args.call:
        _cli_call(args)
    else:
        server = SaturnMCPServer(
            workspace=args.workspace,
            dpaas_home=args.dpaas_home,
            server_name=args.name,
        )
        server.run_stdio()


def _cli_call(args):
    """Direct CLI invocation: python -m mcp.server --call <tool> [args...]"""
    tools = SaturnMCPTools(
        workspace=args.workspace,
        dpaas_home=args.dpaas_home,
    )
    method = getattr(tools, args.call, None)
    if method is None:
        print(f"Unknown tool: {args.call}", file=sys.stderr)
        print(f"Available: {', '.join(t['name'] for t in _TOOL_SCHEMAS)}", file=sys.stderr)
        sys.exit(1)

    # Parse arguments: either a single JSON object or key=value pairs
    kwargs: dict = {}
    if args.tool_args:
        first = args.tool_args[0]
        if first.startswith("{"):
            kwargs = json.loads(" ".join(args.tool_args))
        else:
            for arg in args.tool_args:
                if "=" in arg:
                    k, v = arg.split("=", 1)
                    kwargs[k] = v
                else:
                    kwargs.setdefault("_positional", []).append(arg)

    try:
        result = method(**kwargs)
        print(result)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
