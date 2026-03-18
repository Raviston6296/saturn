"""
Filesystem tools — read, edit, create files and list directories.
"""

from __future__ import annotations

import pathlib


class FilesystemTools:
    """Sandboxed filesystem operations within the workspace."""

    def __init__(self, workspace: str, dry_run: bool = False):
        self.workspace = pathlib.Path(workspace).resolve()
        self.dry_run = dry_run

    def _resolve(self, path: str) -> pathlib.Path:
        """Resolve a relative path within the workspace, with safety check."""
        p = (self.workspace / path).resolve()
        if not str(p).startswith(str(self.workspace)):
            raise PermissionError(f"Path escapes workspace: {path}")
        return p

    def read_file(self, path: str) -> str:
        """Read a file and return contents with line numbers."""
        p = self._resolve(path)
        if not p.exists():
            return f"ERROR: File not found: {path}"
        if not p.is_file():
            return f"ERROR: Not a file: {path}"

        content = p.read_text(encoding="utf-8", errors="replace")
        lines = content.split("\n")

        # Return with line numbers for precise referencing
        numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
        return f"FILE: {path} ({len(lines)} lines)\n{numbered}"

    def edit_file(self, path: str, old_str: str, new_str: str) -> str:
        """Replace exactly one occurrence of old_str with new_str."""
        p = self._resolve(path)
        if not p.exists():
            return f"ERROR: File not found: {path}"

        text = p.read_text(encoding="utf-8")
        count = text.count(old_str)

        if count == 0:
            return (
                f"ERROR: old_str not found in {path}. "
                "Read the file first and match exact whitespace/indentation."
            )
        if count > 1:
            return (
                f"ERROR: old_str found {count} times in {path}. "
                "Make the search string more specific (include more surrounding context)."
            )

        new_text = text.replace(old_str, new_str, 1)
        if not self.dry_run:
            p.write_text(new_text, encoding="utf-8")

        suffix = " [DRY RUN]" if self.dry_run else ""
        return f"OK: Edited {path}{suffix}"

    def create_file(self, path: str, content: str) -> str:
        """Create a new file with the given content."""
        p = self._resolve(path)
        if p.exists():
            return f"WARNING: File already exists, overwriting: {path}"

        p.parent.mkdir(parents=True, exist_ok=True)
        if not self.dry_run:
            p.write_text(content, encoding="utf-8")

        suffix = " [DRY RUN]" if self.dry_run else ""
        return f"OK: Created {path} ({len(content)} bytes){suffix}"

    def list_directory(self, path: str = ".") -> str:
        """List contents of a directory."""
        p = self._resolve(path)
        if not p.exists():
            return f"ERROR: Directory not found: {path}"
        if not p.is_dir():
            return f"ERROR: Not a directory: {path}"

        entries = sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        lines = []
        for entry in entries:
            rel = entry.relative_to(self.workspace)
            if entry.is_dir():
                # Skip hidden and common noise directories
                if entry.name.startswith(".") or entry.name in (
                    "node_modules", "__pycache__", ".git", "venv", ".venv", "dist"
                ):
                    continue
                lines.append(f"  📁 {rel}/")
            else:
                size = entry.stat().st_size
                lines.append(f"  📄 {rel}  ({size:,} bytes)")

        return f"DIRECTORY: {path} ({len(lines)} items)\n" + "\n".join(lines)

