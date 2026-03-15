"""
Saturn ZDPAS Toolkit for Goose.

This extends Goose with tools specifically designed for the ZDPAS
Scala/Java project, giving the AI agent deep domain awareness without
having to rediscover the project structure every time.

If goose-ai is installed as a library, this can be loaded as a toolkit:
    goose run --with-extension saturn_goose.toolkit:SaturnZDPASToolkit "prompt"

If not, it works as a standalone tool provider called from GooseAgent.

Tool categories:
  1. Search tools     — find Scala files and patterns quickly
  2. Context tools    — understand module structure and history
  3. Analysis tools   — parse compiler errors and test failures
  4. Build tools      — check compilation state and DPAAS environment
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from dataclasses import dataclass, field


# ── Data models ──────────────────────────────────────────────────


@dataclass
class ScalaSearchResult:
    """Result of a Scala source file search."""
    file: str
    line_number: int
    line_content: str
    match_context: list[str] = field(default_factory=list)


@dataclass
class CompileError:
    """A single Scala/Java compilation error."""
    file: str
    line: int
    column: int
    message: str
    severity: str = "error"  # error | warning


@dataclass
class TestFailure:
    """A single ScalaTest failure."""
    suite: str
    test_name: str
    message: str
    stack_trace: str = ""


@dataclass
class ModuleContext:
    """Rich context about a ZDPAS module."""
    name: str
    source_files: list[str]
    test_files: list[str]
    total_lines: int
    key_classes: list[str]
    description: str = ""


# ── Tool implementations ──────────────────────────────────────────


class SaturnZDPASTools:
    """
    ZDPAS-specific tools for Goose.

    These tools give Goose deep awareness of the ZDPAS project
    without requiring it to re-read the same files repeatedly.

    Tool design principles:
      - Return structured data that's easy for the LLM to reason about
      - Include enough context to reduce follow-up reads
      - Handle errors gracefully (return empty results, not exceptions)
      - Work from any workspace (ZDPAS worktree)
    """

    def __init__(self, workspace: str):
        self.workspace = Path(workspace).resolve()

    # ── Search ──────────────────────────────────────────────────

    def search_scala_files(
        self,
        pattern: str,
        file_glob: str = "*.scala",
        include_tests: bool = True,
        max_results: int = 20,
    ) -> str:
        """
        Search for a pattern across all Scala source files.

        Returns a formatted string with file paths and matching lines,
        ready for the AI to reason about.

        Args:
            pattern: Text or regex pattern to search for
            file_glob: File glob filter (default: *.scala)
            include_tests: Include test/source/ directory (default: True)
            max_results: Max number of matches to return

        Returns:
            Formatted search results with file:line context
        """
        try:
            search_dirs = ["source"]
            if include_tests:
                search_dirs.append("test/source")

            results: list[ScalaSearchResult] = []

            for search_dir in search_dirs:
                dir_path = self.workspace / search_dir
                if not dir_path.exists():
                    continue

                result = subprocess.run(
                    ["grep", "-rn", "--include", file_glob, pattern, str(dir_path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=str(self.workspace),
                )

                for line in result.stdout.splitlines()[:max_results]:
                    parts = line.split(":", 2)
                    if len(parts) >= 3:
                        file_path = parts[0]
                        try:
                            line_num = int(parts[1])
                        except ValueError:
                            continue
                        content = parts[2]

                        # Make path relative to workspace
                        try:
                            rel_path = str(Path(file_path).relative_to(self.workspace))
                        except ValueError:
                            rel_path = file_path

                        results.append(ScalaSearchResult(
                            file=rel_path,
                            line_number=line_num,
                            line_content=content.strip(),
                        ))

            if not results:
                return f"No matches found for '{pattern}' in {file_glob} files."

            lines = [f"Found {len(results)} match(es) for '{pattern}':\n"]
            for r in results[:max_results]:
                lines.append(f"  {r.file}:{r.line_number}  {r.line_content}")

            return "\n".join(lines)

        except Exception as e:
            return f"Search failed: {e}"

    def get_module_context(self, module_name: str) -> str:
        """
        Get rich context about a ZDPAS module.

        Returns source file list, test file list, key classes, and
        a brief description of what the module does.

        Args:
            module_name: ZDPAS module name (e.g. "transformer", "dataframe")

        Returns:
            Formatted module context string
        """
        module_descriptions = {
            "transformer": "Data transformation operations (join, filter, derive, split, etc.)",
            "dataframe": "DataFrame I/O (CSV, Excel, JSON, XML, Parquet, ZIP, HTML readers/writers)",
            "storage": "Storage abstraction layer (DFS, HDFS, local filesystem)",
            "util": "Utility classes (SQL builders, regex utilities, relative filter helpers)",
            "udf": "User-defined functions for Spark SQL (date, numeric, text, logical)",
            "context": "Job and rule execution context management",
            "query": "Query builders and logical expression parsers",
            "common": "Common models (ZDDataFrameUtil, ZDColumnModelUtil, etc.)",
            "datatype": "Data type utilities and matchers",
            "parquet": "Parquet I/O operations",
            "callback": "Callback handler implementations",
            "widgets": "Widget generation utilities",
            "redis": "Redis utility classes",
        }

        source_dir = self.workspace / "source" / "com" / "zoho" / "dpaas" / module_name
        test_dir = self.workspace / "test" / "source" / "com" / "zoho" / "dpaas" / module_name

        source_files = []
        if source_dir.exists():
            source_files = [
                str(f.relative_to(self.workspace))
                for f in source_dir.rglob("*.scala")
            ] + [
                str(f.relative_to(self.workspace))
                for f in source_dir.rglob("*.java")
            ]

        test_files = []
        if test_dir.exists():
            test_files = [
                str(f.relative_to(self.workspace))
                for f in test_dir.rglob("*.scala")
            ]

        # Count total lines
        total_lines = 0
        for filepath in source_files + test_files:
            try:
                total_lines += sum(
                    1 for _ in (self.workspace / filepath).open()
                )
            except Exception:
                pass

        # Extract key class names
        key_classes = []
        for filepath in source_files[:10]:
            try:
                content = (self.workspace / filepath).read_text(encoding="utf-8")
                classes = re.findall(r'(?:class|object|trait)\s+(\w+)', content)
                key_classes.extend(classes[:5])
            except Exception:
                pass

        description = module_descriptions.get(module_name, f"ZDPAS {module_name} module")

        lines = [
            f"## Module: {module_name}",
            f"Description: {description}",
            f"Total lines: {total_lines:,}",
            f"",
            f"Source files ({len(source_files)}):",
        ]
        for f in source_files[:15]:
            lines.append(f"  {f}")
        if len(source_files) > 15:
            lines.append(f"  ... and {len(source_files) - 15} more")

        lines += ["", f"Test suites ({len(test_files)}):"]
        for f in test_files[:10]:
            lines.append(f"  {f}")

        if key_classes:
            lines += ["", f"Key classes: {', '.join(sorted(set(key_classes))[:15])}"]

        return "\n".join(lines)

    # ── Analysis ─────────────────────────────────────────────────

    def parse_compile_errors(self, scalac_output: str) -> str:
        """
        Parse scalac error output into structured, actionable error list.

        Takes raw scalac/javac output and returns a clean, structured
        list of errors with file, line, and message — no noise.

        Args:
            scalac_output: Raw compiler output (stdout + stderr)

        Returns:
            Structured error list with file:line: message format
        """
        errors: list[CompileError] = []

        # Pattern: /path/to/File.scala:42: error: type mismatch
        scala_pattern = re.compile(
            r'([\w./]+\.(?:scala|java)):(\d+):\s*(error|warning):\s*(.+)',
            re.MULTILINE,
        )

        for match in scala_pattern.finditer(scalac_output):
            filepath, line_str, severity, message = match.groups()
            try:
                rel_path = str(Path(filepath).relative_to(self.workspace))
            except ValueError:
                rel_path = filepath

            errors.append(CompileError(
                file=rel_path,
                line=int(line_str),
                column=0,
                message=message.strip(),
                severity=severity,
            ))

        if not errors:
            # Return raw output if we couldn't parse it
            return f"Could not parse compile output. Raw output:\n{scalac_output[-2000:]}"

        lines = [f"## Compilation {'Errors' if errors else 'OK'} ({len(errors)} issues)\n"]
        for err in errors[:30]:
            icon = "❌" if err.severity == "error" else "⚠️"
            lines.append(f"{icon} {err.file}:{err.line}: {err.message}")

        return "\n".join(lines)

    def parse_test_failures(self, scalatest_output: str) -> str:
        """
        Parse ScalaTest output into structured test failure summaries.

        Args:
            scalatest_output: Raw ScalaTest output (including -oC output)

        Returns:
            Structured failure list with suite, test name, and message
        """
        failures: list[TestFailure] = []

        # Pattern for ScalaTest -oC output
        # "  - test name *** FAILED ***"
        test_fail_pattern = re.compile(
            r'^\s*-\s+(.+?)\s+\*\*\*\s*FAILED\s*\*\*\*',
            re.MULTILINE,
        )

        # "SuiteName:"
        suite_pattern = re.compile(
            r'^(\w+Suite|[\w.]+):',
            re.MULTILINE,
        )

        current_suite = "Unknown"
        for line in scalatest_output.splitlines():
            suite_match = suite_pattern.match(line.strip())
            if suite_match:
                current_suite = suite_match.group(1)

            fail_match = test_fail_pattern.match(line)
            if fail_match:
                test_name = fail_match.group(1).strip()
                failures.append(TestFailure(
                    suite=current_suite,
                    test_name=test_name,
                    message="",
                ))

        # Extract failure messages (follow FAILED lines)
        lines = scalatest_output.splitlines()
        for i, line in enumerate(lines):
            if "*** FAILED ***" in line:
                # Grab up to 5 lines after for context
                context = []
                for j in range(i + 1, min(i + 6, len(lines))):
                    ctx_line = lines[j].strip()
                    if ctx_line and not ctx_line.startswith("- "):
                        context.append(ctx_line)
                    else:
                        break
                # Update the last failure's message
                if failures:
                    failures[-1].message = "\n".join(context)

        if not failures:
            return f"No test failures found (or output format not recognized).\nOutput tail:\n{scalatest_output[-1000:]}"

        result_lines = [f"## Test Failures ({len(failures)} failed)\n"]
        for failure in failures[:20]:
            result_lines.append(f"❌ {failure.suite} — {failure.test_name}")
            if failure.message:
                for msg_line in failure.message.splitlines()[:3]:
                    result_lines.append(f"   {msg_line}")
            result_lines.append("")

        return "\n".join(result_lines)

    def get_project_structure(self) -> str:
        """
        Return a compact overview of the ZDPAS project structure.

        Shows all modules with file counts, helping the agent quickly
        understand the codebase layout.
        """
        source_root = self.workspace / "source" / "com" / "zoho" / "dpaas"
        test_root = self.workspace / "test" / "source" / "com" / "zoho" / "dpaas"

        lines = ["## ZDPAS Project Structure\n"]

        if source_root.exists():
            lines.append("### Source modules (source/com/zoho/dpaas/):")
            for module_dir in sorted(source_root.iterdir()):
                if module_dir.is_dir():
                    scala_count = len(list(module_dir.rglob("*.scala")))
                    java_count = len(list(module_dir.rglob("*.java")))
                    total = scala_count + java_count
                    lines.append(
                        f"  {module_dir.name:<20} "
                        f"({total} files: {scala_count} Scala, {java_count} Java)"
                    )

        if test_root.exists():
            lines.append("\n### Test modules (test/source/com/zoho/dpaas/):")
            for module_dir in sorted(test_root.iterdir()):
                if module_dir.is_dir():
                    suite_count = len(list(module_dir.rglob("*Suite.scala")))
                    lines.append(f"  {module_dir.name:<20} ({suite_count} test suites)")

        # Resources
        resources = self.workspace / "resources"
        if resources.exists():
            res_files = list(resources.iterdir())
            lines.append(f"\n### Resources (resources/): {len(res_files)} files")

        return "\n".join(lines)

    def get_changed_file_context(self, changed_files: list[str]) -> str:
        """
        Get context about changed files: which modules, what classes, test coverage.

        Used to tell the agent exactly what was changed and what tests to focus on.

        Args:
            changed_files: List of changed file paths (relative to workspace)

        Returns:
            Structured context about the changed files
        """
        from gates.incremental import get_affected_modules_zdpas

        affected_modules = get_affected_modules_zdpas(changed_files)

        lines = [f"## Changed Files Context\n"]
        lines.append(f"Files changed: {len(changed_files)}")
        for f in changed_files[:20]:
            lines.append(f"  • {f}")

        if affected_modules:
            lines.append(f"\nAffected modules: {', '.join(sorted(affected_modules))}")
            lines.append("Tests to run after your fix:")
            for module in sorted(affected_modules):
                lines.append(f"  • com.zoho.dpaas.{module}.*")

        return "\n".join(lines)


# ── Goose toolkit interface ───────────────────────────────────────


def get_tools_description() -> str:
    """
    Return a description of available Saturn ZDPAS tools.
    Used to inject tool awareness into the Goose system prompt.
    """
    return """
## Saturn ZDPAS Tools Available

You have access to these Saturn-specific tools via the MCP server:

**search_scala_files(pattern, file_glob="*.scala", include_tests=True)**
  Search for a pattern across all Scala/Java source files.
  Example: search_scala_files("ZDTrimSuite")

**get_module_context(module_name)**
  Get rich context about a ZDPAS module (file list, classes, test suites).
  Example: get_module_context("transformer")

**parse_compile_errors(scalac_output)**
  Parse raw scalac output into structured error list.

**parse_test_failures(scalatest_output)**
  Parse ScalaTest output into structured failure list.

**get_project_structure()**
  Get an overview of all ZDPAS modules with file counts.

**get_changed_file_context(changed_files)**
  Get context about what changed and which tests are affected.
""".strip()
