"""
Terminal tools — execute shell commands safely in the workspace.
"""

from __future__ import annotations

import subprocess
import pathlib


# Commands that are always blocked
BLOCKED_PATTERNS = [
    "rm -rf /",
    "rm -rf ~",
    "DROP TABLE",
    "DROP DATABASE",
    "format c:",
    ":(){:|:&};:",
    "mkfs.",
    "> /dev/sda",
    "dd if=/dev/zero",
]

# Max output length returned to the agent (avoid token waste)
MAX_OUTPUT_LENGTH = 8000

# Command timeout in seconds
COMMAND_TIMEOUT = 120


class TerminalTools:
    """Safe shell command execution within the workspace."""

    def __init__(self, workspace: str):
        self.workspace = pathlib.Path(workspace).resolve()

    def run_command(self, command: str, cwd: str | None = None) -> str:
        """
        Execute a shell command and return stdout + stderr + exit code.
        Blocks known-dangerous commands. Caps output length.
        """
        # Safety: block destructive patterns
        for pattern in BLOCKED_PATTERNS:
            if pattern in command:
                return f"BLOCKED: Dangerous command detected — contains '{pattern}'"

        run_dir = (self.workspace / cwd) if cwd else self.workspace

        if not run_dir.exists():
            return f"ERROR: Working directory does not exist: {cwd}"

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=run_dir,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT,
                env=self._safe_env(),
            )

            output = (result.stdout + result.stderr).strip()
            exit_code = result.returncode

            # Truncate if too long
            if len(output) > MAX_OUTPUT_LENGTH:
                output = (
                    output[:MAX_OUTPUT_LENGTH // 2]
                    + f"\n\n... [TRUNCATED — {len(output)} chars total] ...\n\n"
                    + output[-MAX_OUTPUT_LENGTH // 2:]
                )

            return f"EXIT CODE: {exit_code}\n{output}"

        except subprocess.TimeoutExpired:
            return f"ERROR: Command timed out after {COMMAND_TIMEOUT}s: {command}"
        except Exception as e:
            return f"ERROR: Failed to run command: {e}"

    def _safe_env(self) -> dict[str, str]:
        """Build an environment dict, stripping sensitive vars from leaking."""
        import os
        env = os.environ.copy()
        # Don't leak API keys into subprocess output
        for key in list(env.keys()):
            if any(s in key.upper() for s in ["SECRET", "PASSWORD", "PRIVATE_KEY"]):
                env[key] = "***REDACTED***"
        return env

