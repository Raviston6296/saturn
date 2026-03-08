"""
Tool registry — defines all tool schemas and routes execution.
"""

from __future__ import annotations

from tools.filesystem import FilesystemTools
from tools.terminal import TerminalTools
from tools.git import GitTools
from tools.gitlab import GitLabTools
from tools.search import SearchTools


# ━━━ Tool schemas sent to Claude API ━━━━━━━━━━━━━━━━━━━━━━━━━━

TOOL_SCHEMAS = [
    # ── Filesystem ──
    {
        "name": "read_file",
        "description": "Read the full contents of a file with line numbers. Always call this before editing a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path from workspace root"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "edit_file",
        "description": "Replace EXACTLY one occurrence of old_str with new_str in a file. The old_str must match the file content exactly — whitespace, indentation, everything.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path"},
                "old_str": {"type": "string", "description": "Exact text to find and replace (must appear exactly once)"},
                "new_str": {"type": "string", "description": "Replacement text"},
            },
            "required": ["path", "old_str", "new_str"]
        }
    },
    {
        "name": "create_file",
        "description": "Create a new file with the given content. Parent directories will be created automatically.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path to create"},
                "content": {"type": "string", "description": "File content"},
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "list_directory",
        "description": "List files and directories at a given path. Returns names with / suffix for directories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative directory path (default: '.')", "default": "."}
            },
            "required": []
        }
    },

    # ── Terminal ──
    {
        "name": "run_command",
        "description": "Execute a shell command in the workspace. Returns stdout + stderr + exit code. Use for running tests, builds, linters, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "cwd": {"type": "string", "description": "Working directory relative to workspace (optional)"},
            },
            "required": ["command"]
        }
    },

    # ── Search ──
    {
        "name": "search_in_files",
        "description": "Search for a string or regex pattern across all files in the workspace. Returns matching lines with file paths and line numbers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Search pattern (string or regex)"},
                "directory": {"type": "string", "description": "Directory to search in (default: '.')", "default": "."},
                "file_glob": {"type": "string", "description": "File pattern filter, e.g. '*.py' or '*.ts'"},
            },
            "required": ["pattern"]
        }
    },

    # ── Git ──
    {
        "name": "git_status",
        "description": "Show current git status (modified, staged, untracked files).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "git_diff",
        "description": "Show the current git diff of all changes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Specific file to diff (optional — diffs all if omitted)"}
            },
            "required": []
        }
    },
    {
        "name": "git_commit",
        "description": "Stage all changes and create a git commit with the given message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Commit message in conventional commit format: 'type: description'"}
            },
            "required": ["message"]
        }
    },
    {
        "name": "git_push",
        "description": "Push the current branch to the remote origin.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },

    # ── GitLab ──
    {
        "name": "create_merge_request",
        "description": "Create a GitLab Merge Request from the current branch to the default branch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "MR title"},
                "body": {"type": "string", "description": "MR description in Markdown"},
                "base_branch": {"type": "string", "description": "Target branch to merge into (default: from config)", "default": ""},
            },
            "required": ["title", "body"]
        }
    },
]


class ToolExecutor:
    """Routes tool calls to the correct implementation."""

    def __init__(self, workspace: str, repo_name: str = "", dry_run: bool = False):
        self.workspace = workspace
        self.dry_run = dry_run
        self.fs = FilesystemTools(workspace, dry_run)
        self.terminal = TerminalTools(workspace)
        self.git = GitTools(workspace)
        self.gitlab = GitLabTools(repo_name)
        self.search = SearchTools(workspace)
        self.log: list[dict] = []

    def execute(self, tool_name: str, inputs: dict) -> str:
        """Execute a tool by name and return the result string."""
        self.log.append({"tool": tool_name, "inputs": inputs})

        handlers = {
            "read_file": self.fs.read_file,
            "edit_file": self.fs.edit_file,
            "create_file": self.fs.create_file,
            "list_directory": self.fs.list_directory,
            "run_command": self.terminal.run_command,
            "search_in_files": self.search.search_in_files,
            "git_status": self.git.status,
            "git_diff": self.git.diff,
            "git_commit": self.git.commit,
            "git_push": self.git.push,
            "create_merge_request": self.gitlab.create_merge_request,
        }

        handler = handlers.get(tool_name)
        if not handler:
            return f"ERROR: Unknown tool '{tool_name}'"

        try:
            return handler(**inputs)
        except Exception as e:
            return f"ERROR in {tool_name}: {e}"

