"""
Tests for the Saturn MCP server and quick compilation tools.

Tests are structured to be fast and self-contained — no real scalac
or Goose binary is needed.  All subprocess calls are mocked.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch
import subprocess

import pytest


# ── QuickCompileResult tests ─────────────────────────────────────


class TestQuickCompileResult:
    def test_success_format(self):
        from mcp.quick_compile import QuickCompileResult
        r = QuickCompileResult(
            success=True, files_compiled=2, duration_seconds=8.3
        )
        msg = r.format_errors()
        assert "✅" in msg
        assert "2 file" in msg
        assert "8.3s" in msg

    def test_error_format(self):
        from mcp.quick_compile import QuickCompileResult, QuickCompileError
        r = QuickCompileResult(
            success=False,
            errors=[
                QuickCompileError(
                    file="source/com/zoho/dpaas/transformer/ZDFilter.scala",
                    line=42,
                    column=10,
                    severity="error",
                    message="type mismatch",
                )
            ],
            files_compiled=1,
            duration_seconds=12.1,
        )
        msg = r.format_errors()
        assert "❌" in msg
        assert "ZDFilter.scala:42" in msg
        assert "type mismatch" in msg

    def test_warnings_only(self):
        from mcp.quick_compile import QuickCompileResult, QuickCompileError
        r = QuickCompileResult(
            success=True,
            warnings=[
                QuickCompileError(
                    file="source/Foo.scala",
                    line=10,
                    column=0,
                    severity="warning",
                    message="deprecated API",
                )
            ],
            files_compiled=1,
            duration_seconds=5.0,
        )
        msg = r.format_errors()
        assert "⚠️" in msg
        assert "deprecated API" in msg

    def test_max_errors_truncation(self):
        from mcp.quick_compile import QuickCompileResult, QuickCompileError
        errors = [
            QuickCompileError(file=f"f{i}.scala", line=i, column=0,
                              severity="error", message="err")
            for i in range(30)
        ]
        r = QuickCompileResult(success=False, errors=errors, files_compiled=30)
        msg = r.format_errors(max_errors=5)
        assert "and 25 more" in msg


# ── QuickCompiler unit tests ──────────────────────────────────────


class TestQuickCompiler:
    """Tests for QuickCompiler — no real scalac invocation."""

    def test_resolve_files_filters_nonexistent(self, tmp_path):
        from mcp.quick_compile import QuickCompiler
        # Create one real file
        scala_file = tmp_path / "Foo.scala"
        scala_file.write_text("object Foo")
        compiler = QuickCompiler(workspace=str(tmp_path))
        resolved = compiler._resolve_files([
            "Foo.scala",
            "nonexistent/Bar.scala",
            "Foo.java",   # wrong extension and doesn't exist
        ])
        assert len(resolved) == 1
        assert "Foo.scala" in resolved[0]

    def test_build_classpath_falls_back_gracefully(self, tmp_path):
        from mcp.quick_compile import QuickCompiler
        compiler = QuickCompiler(
            workspace=str(tmp_path),
            dpaas_home="/nonexistent/dpaas",
        )
        cp = compiler._build_classpath()
        # Should not raise; may be "." as fallback
        assert isinstance(cp, str)

    def test_parse_output_extracts_errors(self, tmp_path):
        from mcp.quick_compile import QuickCompiler
        compiler = QuickCompiler(workspace=str(tmp_path))
        output = (
            f"{tmp_path}/source/ZDFilter.scala:42: error: type mismatch\n"
            f"  found   : String\n"
            f"  required: Int\n"
            f"{tmp_path}/source/ZDUtil.scala:17: warning: deprecated\n"
        )
        errors, warnings = compiler._parse_output(output, [])
        assert len(errors) == 1
        assert errors[0].line == 42
        assert "type mismatch" in errors[0].message
        assert len(warnings) == 1
        assert warnings[0].line == 17

    def test_compile_no_scala_files_returns_error(self, tmp_path):
        from mcp.quick_compile import QuickCompiler
        compiler = QuickCompiler(workspace=str(tmp_path))
        result = compiler.compile([])
        assert result.success is False
        assert "No valid" in result.raw_output

    def test_compile_calls_scalac(self, tmp_path):
        from mcp.quick_compile import QuickCompiler
        scala_file = tmp_path / "Foo.scala"
        scala_file.write_text("object Foo {}")

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stdout = ""
        fake_proc.stderr = ""

        with patch("subprocess.run", return_value=fake_proc) as mock_run:
            compiler = QuickCompiler(workspace=str(tmp_path))
            result = compiler.compile(["Foo.scala"])

        mock_run.assert_called_once()
        cmd_args = mock_run.call_args[0][0]
        assert "scalac" in cmd_args
        assert result.success is True

    def test_compile_scalac_timeout(self, tmp_path):
        from mcp.quick_compile import QuickCompiler
        scala_file = tmp_path / "Foo.scala"
        scala_file.write_text("object Foo {}")

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("scalac", 120)):
            compiler = QuickCompiler(workspace=str(tmp_path))
            result = compiler.compile(["Foo.scala"])

        assert result.success is False
        assert "timed out" in result.raw_output

    def test_compile_scalac_not_found(self, tmp_path):
        from mcp.quick_compile import QuickCompiler
        scala_file = tmp_path / "Foo.scala"
        scala_file.write_text("object Foo {}")

        with patch("subprocess.run", side_effect=FileNotFoundError("scalac")):
            compiler = QuickCompiler(workspace=str(tmp_path))
            result = compiler.compile(["Foo.scala"])

        assert result.success is False
        assert "not found" in result.raw_output

    def test_clear_cache(self, tmp_path):
        from mcp.quick_compile import QuickCompiler
        compiler = QuickCompiler(workspace=str(tmp_path))
        # Create a fake .class file in cache
        fake_class = compiler.cache_dir / "Foo.class"
        fake_class.write_bytes(b"fake")
        assert fake_class.exists()
        compiler.clear_cache()
        assert not fake_class.exists()

    def test_compile_module_missing_dir(self, tmp_path):
        from mcp.quick_compile import QuickCompiler
        compiler = QuickCompiler(workspace=str(tmp_path))
        result = compiler.compile_module("nonexistent_module")
        assert result.success is False
        assert "not found" in result.raw_output


# ── SaturnMCPTools tests ──────────────────────────────────────────


class TestSaturnMCPTools:
    def _make_tools(self, tmp_path):
        from mcp.server import SaturnMCPTools
        return SaturnMCPTools(workspace=str(tmp_path))

    def test_get_project_info_no_source(self, tmp_path):
        tools = self._make_tools(tmp_path)
        info = tools.get_project_info()
        # Should not raise; returns whatever structure there is
        assert isinstance(info, str)

    def test_get_module_context_missing(self, tmp_path):
        tools = self._make_tools(tmp_path)
        ctx = tools.get_module_context("transformer")
        assert "transformer" in ctx

    def test_search_code_no_match(self, tmp_path):
        tools = self._make_tools(tmp_path)
        result = tools.search_code("__nonexistent_pattern_xyz__")
        assert "No matches" in result

    def test_get_changed_files_clean_repo(self, tmp_path):
        # Initialise a bare git repo so git diff works
        import subprocess as sp
        sp.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
        sp.run(["git", "commit", "--allow-empty", "-m", "init"],
               cwd=str(tmp_path), capture_output=True,
               env={**os.environ, "GIT_AUTHOR_NAME": "test",
                    "GIT_AUTHOR_EMAIL": "t@t.com",
                    "GIT_COMMITTER_NAME": "test",
                    "GIT_COMMITTER_EMAIL": "t@t.com"})
        from mcp.server import SaturnMCPTools
        tools = SaturnMCPTools(workspace=str(tmp_path))
        result = tools.get_changed_files()
        assert "No changed files" in result or "Changed" in result or "Files" in result

    def test_get_dpaas_env_missing_home(self, tmp_path):
        tools = self._make_tools(tmp_path)
        with patch.dict(os.environ, {"DPAAS_HOME": "/nonexistent"}, clear=False):
            result = tools.get_dpaas_env()
        assert "DPAAS_HOME" in result
        assert "/nonexistent" in result

    def test_compile_quick_delegates_to_compiler(self, tmp_path):
        tools = self._make_tools(tmp_path)
        from mcp.quick_compile import QuickCompileResult
        with patch("mcp.quick_compile.QuickCompiler.compile",
                   return_value=QuickCompileResult(success=True, files_compiled=1)):
            result = tools.compile_quick(["Foo.scala"])
        assert "✅" in result

    def test_run_module_tests_no_jars(self, tmp_path):
        tools = self._make_tools(tmp_path)
        result = tools.run_module_tests("transformer")
        assert "❌" in result
        assert "dpaas.jar" in result


# ── SaturnMCPServer protocol tests ───────────────────────────────


class TestSaturnMCPServerProtocol:
    """Test the JSON-RPC protocol layer of the MCP server."""

    def _make_server(self, tmp_path):
        from mcp.server import SaturnMCPServer
        return SaturnMCPServer(workspace=str(tmp_path))

    def test_initialize(self, tmp_path):
        server = self._make_server(tmp_path)
        req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        resp = server._handle(req)
        assert resp["result"]["protocolVersion"] == server.PROTOCOL_VERSION
        assert resp["result"]["serverInfo"]["name"] == "saturn-zdpas"

    def test_initialized_notification_returns_none(self, tmp_path):
        server = self._make_server(tmp_path)
        req = {"jsonrpc": "2.0", "method": "initialized"}
        resp = server._handle(req)
        assert resp is None

    def test_tools_list(self, tmp_path):
        server = self._make_server(tmp_path)
        req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        resp = server._handle(req)
        tools = resp["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        assert "compile_quick" in tool_names
        assert "run_module_tests" in tool_names
        assert "search_code" in tool_names
        assert "get_project_info" in tool_names
        assert "get_module_context" in tool_names
        assert "get_changed_files" in tool_names
        assert "get_dpaas_env" in tool_names

    def test_tools_call_compile_quick(self, tmp_path):
        server = self._make_server(tmp_path)
        from mcp.quick_compile import QuickCompileResult
        with patch("mcp.quick_compile.QuickCompiler.compile",
                   return_value=QuickCompileResult(success=True, files_compiled=1)):
            req = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "compile_quick",
                    "arguments": {"files": ["source/Foo.scala"]},
                },
            }
            resp = server._handle(req)
        content = resp["result"]["content"][0]["text"]
        assert "✅" in content
        assert resp["result"]["isError"] is False

    def test_tools_call_unknown_tool(self, tmp_path):
        server = self._make_server(tmp_path)
        req = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "does_not_exist", "arguments": {}},
        }
        resp = server._handle(req)
        assert "error" in resp
        assert "Unknown tool" in resp["error"]["message"]

    def test_unknown_method(self, tmp_path):
        server = self._make_server(tmp_path)
        req = {"jsonrpc": "2.0", "id": 5, "method": "unknown/method"}
        resp = server._handle(req)
        assert resp["error"]["code"] == -32601

    def test_ping(self, tmp_path):
        server = self._make_server(tmp_path)
        req = {"jsonrpc": "2.0", "id": 6, "method": "ping"}
        resp = server._handle(req)
        assert resp["result"] == {}


# ── GooseAgent quick_compile integration ─────────────────────────


class TestGooseAgentQuickCompile:
    """Verify GooseAgent exposes compile_quick for Tier 1 feedback."""

    def test_quick_compile_method_exists(self, tmp_path):
        from agent.goose_agent import GooseAgent
        from unittest.mock import patch

        with patch.object(GooseAgent, "_setup_profile"):
            agent = GooseAgent.__new__(GooseAgent)
            agent.workspace = str(tmp_path)
            agent._tools = MagicMock()
            agent._cli = MagicMock()

        from mcp.quick_compile import QuickCompileResult
        with patch("mcp.quick_compile.QuickCompiler.compile",
                   return_value=QuickCompileResult(success=True, files_compiled=1)):
            with patch.dict(os.environ, {"DPAAS_HOME": str(tmp_path)}):
                result = agent.quick_compile(
                    ["source/com/zoho/dpaas/transformer/ZDFilter.scala"]
                )
        assert "✅" in result
