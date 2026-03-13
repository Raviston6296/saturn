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

        classpath = ":".join([
            str(self.workspace / "dpaas_test.jar"),
            str(self.workspace / "dpaas.jar"),
            str(self.dpaas_home / "zdpas" / "spark" / "jars" / "*"),
            str(self.dpaas_home / "zdpas" / "spark" / "app_blue" / "ExpParser.jar"),
            str(self.dpaas_home / "zdpas" / "spark" / "lib" / "*"),
        ])

        cmd = (
            f"java -cp \"{classpath}\" "
            f"-Xmx3g "
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

    PROTOCOL_VERSION = "2024-11-05"

    def __init__(self, workspace: str = ".", dpaas_home: str = ""):
        self.tools = SaturnMCPTools(workspace=workspace, dpaas_home=dpaas_home)
        self._tool_map = {t["name"]: t for t in _TOOL_SCHEMAS}

    def run_stdio(self):
        """
        Run the MCP server in stdio mode (called by Goose as a subprocess).

        Reads JSON-RPC requests from stdin, writes responses to stdout.
        Runs until stdin is closed.
        """
        import sys

        while True:
            try:
                line = sys.stdin.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                request = json.loads(line)
            except (json.JSONDecodeError, EOFError, KeyboardInterrupt):
                break

            response = self._handle(request)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()

    def _handle(self, req: dict) -> dict | None:
        """Dispatch a JSON-RPC request to the appropriate MCP handler."""
        method = req.get("method", "")
        req_id = req.get("id")
        params = req.get("params", {})

        try:
            if method == "initialize":
                result = self._handle_initialize(params)
            elif method == "initialized":
                return None  # notification, no response
            elif method == "tools/list":
                result = {"tools": _TOOL_SCHEMAS}
            elif method == "tools/call":
                result = self._handle_tool_call(params)
            elif method == "ping":
                result = {}
            else:
                # Unknown method — return error
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
            "capabilities": {"tools": {}},
            "serverInfo": {
                "name": "saturn-zdpas",
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
    """Start the Saturn MCP server in stdio mode."""
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
    args = parser.parse_args()

    server = SaturnMCPServer(
        workspace=args.workspace,
        dpaas_home=args.dpaas_home,
    )
    server.run_stdio()


if __name__ == "__main__":
    main()
