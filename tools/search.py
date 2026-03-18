"""
Search tools — grep-based code search across the workspace.
"""

from __future__ import annotations

import subprocess
import pathlib


class SearchTools:
    """Code search within the workspace."""

    def __init__(self, workspace: str):
        self.workspace = pathlib.Path(workspace).resolve()

    def search_in_files(
        self,
        pattern: str,
        directory: str = ".",
        file_glob: str | None = None,
    ) -> str:
        """
        Search for a pattern across files using grep.
        Returns matching lines with file paths and line numbers.
        """
        search_dir = self.workspace / directory

        # Build grep command
        # Try ripgrep first (faster), fall back to grep
        cmd = self._build_search_cmd(pattern, str(search_dir), file_glob)

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
            )

            output = result.stdout.strip()
            if not output:
                return f"NO MATCHES found for pattern: '{pattern}'"

            # Cap output
            lines = output.split("\n")
            if len(lines) > 100:
                output = "\n".join(lines[:100])
                output += f"\n\n... [{len(lines)} total matches — showing first 100]"

            return f"SEARCH RESULTS for '{pattern}':\n{output}"

        except subprocess.TimeoutExpired:
            return f"ERROR: Search timed out for pattern: '{pattern}'"
        except Exception as e:
            return f"ERROR: Search failed: {e}"

    def _build_search_cmd(
        self, pattern: str, directory: str, file_glob: str | None
    ) -> str:
        """Build the search command, preferring ripgrep if available."""
        # Escape single quotes in pattern
        safe_pattern = pattern.replace("'", "'\\''")

        # Try ripgrep first
        rg_cmd = f"rg -n --no-heading '{safe_pattern}' {directory}"
        if file_glob:
            rg_cmd += f" -g '{file_glob}'"

        # Fallback to grep
        grep_cmd = f"grep -rn '{safe_pattern}' {directory}"
        if file_glob:
            grep_cmd += f" --include='{file_glob}'"

        # Exclude common noise directories
        grep_cmd += (
            " --exclude-dir=node_modules"
            " --exclude-dir=.git"
            " --exclude-dir=__pycache__"
            " --exclude-dir=.venv"
            " --exclude-dir=dist"
        )

        # Use ripgrep if available, else grep
        return f"({rg_cmd}) 2>/dev/null || ({grep_cmd}) 2>/dev/null"

