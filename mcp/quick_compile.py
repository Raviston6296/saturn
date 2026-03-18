"""
Quick incremental compilation for ZDPAS.

Compiles only the specified Scala/Java files against the already-extracted
DPAAS classpath in DPAAS_HOME. No JAR is produced — this is a syntax and
type-checking pass only.

Typical timings:
    1 changed Scala file   →  5–15 s   (vs 2–5 min for full gate compile)
    5 changed Scala files  → 15–40 s
    Full module            →  1–2 min  (vs 2–5 min full compile)

How it works:
    1. Verify DPAAS_HOME is populated (jars present from 'setup' gate)
    2. Build a minimal classpath: DPAAS_HOME jars + any previously compiled
       classes cached in /tmp/saturn_quick_classes/<workspace_hash>/
    3. Run scalac on just the provided files
    4. Parse stdout/stderr for errors and return structured results
    5. On success, copy .class files into the quick-check cache so subsequent
       calls can build on them (incremental within a session)

This is Tier 1 of the three-tier feedback loop:
    Tier 1 → compile_quick  (this module)
    Tier 2 → run_module_tests
    Tier 3 → full 4-stage gate pipeline
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


# ── Data models ──────────────────────────────────────────────────


@dataclass
class QuickCompileError:
    """A single scalac/javac error from a quick compile run."""
    file: str          # relative path to workspace
    line: int
    column: int
    severity: str      # "error" | "warning"
    message: str


@dataclass
class QuickCompileResult:
    """Result of a quick incremental compilation."""
    success: bool
    errors: list[QuickCompileError] = field(default_factory=list)
    warnings: list[QuickCompileError] = field(default_factory=list)
    files_compiled: int = 0
    duration_seconds: float = 0.0
    raw_output: str = ""

    def format_errors(self, max_errors: int = 20) -> str:
        """Return a clean, AI-readable error summary."""
        if self.success and not self.warnings:
            return f"✅ Quick compile passed ({self.files_compiled} file(s), {self.duration_seconds:.1f}s)"

        lines = []
        if self.errors:
            lines.append(
                f"❌ Quick compile: {len(self.errors)} error(s) "
                f"({self.files_compiled} file(s), {self.duration_seconds:.1f}s)\n"
            )
            for err in self.errors[:max_errors]:
                lines.append(f"  {err.file}:{err.line}: {err.message}")
            if len(self.errors) > max_errors:
                lines.append(f"  ... and {len(self.errors) - max_errors} more errors")
        elif self.warnings:
            lines.append(
                f"⚠️  Quick compile: {len(self.warnings)} warning(s) "
                f"({self.files_compiled} file(s), {self.duration_seconds:.1f}s)\n"
            )
            for w in self.warnings[:5]:
                lines.append(f"  {w.file}:{w.line}: {w.message}")

        return "\n".join(lines)


# ── Quick compiler ────────────────────────────────────────────────


class QuickCompiler:
    """
    Fast incremental Scala/Java compiler for ZDPAS.

    Maintains a per-workspace class cache so repeated calls within a
    Goose session are progressively faster (each call builds on the
    previous compiled .class files).
    """

    def __init__(self, workspace: str, dpaas_home: str = ""):
        self.workspace = Path(workspace).resolve()
        self.dpaas_home = Path(
            dpaas_home
            or os.environ.get("DPAAS_HOME", "")
            or "/opt/dpaas"
        )

        # Per-workspace class cache in /tmp — survives within a session
        ws_hash = hashlib.md5(str(self.workspace).encode()).hexdigest()[:8]
        self.cache_dir = Path(tempfile.gettempdir()) / f"saturn_qc_{ws_hash}"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def compile(
        self,
        files: list[str],
        timeout: int = 120,
    ) -> QuickCompileResult:
        """
        Compile the specified source files and return structured results.

        Args:
            files: Relative paths to .scala/.java files (relative to workspace)
            timeout: Maximum seconds to wait (default 120)

        Returns:
            QuickCompileResult with errors, warnings, and timing
        """
        import time

        # Resolve and validate files
        abs_files = self._resolve_files(files)
        if not abs_files:
            return QuickCompileResult(
                success=False,
                raw_output="No valid .scala/.java files provided.",
            )

        # Split by language
        scala_files = [f for f in abs_files if f.endswith(".scala")]
        java_files = [f for f in abs_files if f.endswith(".java")]

        classpath = self._build_classpath()
        start = time.time()

        # Write file list to a temp file (scalac @file syntax)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as flist:
            flist.write("\n".join(scala_files + java_files))
            flist_path = flist.name

        try:
            result = self._run_scalac(flist_path, classpath, timeout)
        finally:
            Path(flist_path).unlink(missing_ok=True)

        duration = time.time() - start
        errors, warnings = self._parse_output(result.stdout + result.stderr, abs_files)

        return QuickCompileResult(
            success=(result.returncode == 0),
            errors=errors,
            warnings=warnings,
            files_compiled=len(abs_files),
            duration_seconds=round(duration, 1),
            raw_output=(result.stdout + result.stderr)[:4000],
        )

    def compile_module(
        self,
        module_name: str,
        timeout: int = 180,
    ) -> QuickCompileResult:
        """
        Compile all sources in a ZDPAS module.

        Useful for checking whether a module compiles cleanly after
        the agent's changes, before running ScalaTest.

        Args:
            module_name: ZDPAS module name (e.g. "transformer", "util")
            timeout: Maximum seconds (default 180)
        """
        module_dir = (
            self.workspace
            / "source"
            / "com"
            / "zoho"
            / "dpaas"
            / module_name
        )
        if not module_dir.exists():
            return QuickCompileResult(
                success=False,
                raw_output=f"Module directory not found: {module_dir}",
            )

        files = [
            str(f.relative_to(self.workspace))
            for f in module_dir.rglob("*.scala")
        ] + [
            str(f.relative_to(self.workspace))
            for f in module_dir.rglob("*.java")
        ]

        return self.compile(files, timeout=timeout)

    # ── Private helpers ───────────────────────────────────────────

    def _build_classpath(self) -> str:
        """
        Build the classpath for quick compilation.

        Includes:
          - DPAAS_HOME jars (from 'setup' gate extraction)
          - The quick-check class cache (from previous compile calls)
          - lib/ directory
        """
        parts = []

        jars_dir = self.dpaas_home / "zdpas" / "spark" / "jars"
        lib_dir = self.dpaas_home / "zdpas" / "spark" / "lib"

        if jars_dir.exists():
            parts.append(str(jars_dir / "*"))
        if lib_dir.exists():
            parts.append(str(lib_dir / "*"))

        # Add previously compiled classes (incremental within session)
        if any(self.cache_dir.rglob("*.class")):
            parts.append(str(self.cache_dir))

        if not parts:
            # Fallback: try to find jars in common locations
            for candidate in ["/opt/dpaas", "/data/saturn/dpaas"]:
                candidate_jars = Path(candidate) / "zdpas" / "spark" / "jars"
                if candidate_jars.exists():
                    parts.append(str(candidate_jars / "*"))
                    break

        return ":".join(parts) if parts else "."

    def _resolve_files(self, files: list[str]) -> list[str]:
        """Resolve relative file paths to absolute, filtering to existing files."""
        resolved = []
        for f in files:
            p = Path(f)
            if not p.is_absolute():
                p = self.workspace / p
            if p.exists() and p.suffix in (".scala", ".java"):
                resolved.append(str(p))
        return resolved

    def _run_scalac(
        self,
        file_list_path: str,
        classpath: str,
        timeout: int,
    ) -> subprocess.CompletedProcess:
        """Run scalac with the quick-check class output directory."""
        cmd = [
            "scalac",
            "-cp", classpath,
            "-J-Xmx1g",              # smaller heap for quick checks
            "-d", str(self.cache_dir),
            f"@{file_list_path}",
        ]
        try:
            return subprocess.run(
                cmd,
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=-1,
                stdout="",
                stderr=f"scalac timed out after {timeout}s",
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=-1,
                stdout="",
                stderr="scalac not found on PATH",
            )

    def _parse_output(
        self,
        output: str,
        abs_files: list[str],
    ) -> tuple[list[QuickCompileError], list[QuickCompileError]]:
        """Parse scalac output into structured errors and warnings."""
        errors: list[QuickCompileError] = []
        warnings: list[QuickCompileError] = []

        # Pattern: /abs/path/File.scala:42: error: type mismatch
        pattern = re.compile(
            r"([\w./\-]+\.(?:scala|java)):(\d+):\s*(error|warning):\s*(.+)",
        )

        for match in pattern.finditer(output):
            filepath, line_str, severity, message = match.groups()
            try:
                rel_path = str(Path(filepath).relative_to(self.workspace))
            except ValueError:
                rel_path = filepath

            entry = QuickCompileError(
                file=rel_path,
                line=int(line_str),
                column=0,
                severity=severity,
                message=message.strip(),
            )
            if severity == "error":
                errors.append(entry)
            else:
                warnings.append(entry)

        return errors, warnings

    def clear_cache(self):
        """Remove the quick-check class cache (e.g. after a new setup gate run)."""
        import shutil
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir, ignore_errors=True)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
