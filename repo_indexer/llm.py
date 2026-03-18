"""
LLM integration — calls Cursor Agent CLI to answer code questions.

Replaces the old Ollama/Qwen integration. All AI work is now
delegated to the Cursor Agent CLI (`agent`).

Install:
    curl https://cursor.com/install -fsS | bash

Public API
----------
ask_cursor(question, context, repo_path)  → str
ask_qwen(question, context)               → str  (backward-compat alias)
"""

from __future__ import annotations

import os
import re
import subprocess

from rich.console import Console

from repo_indexer.config import CURSOR_CLI_PATH, REPO_PATH

_console = Console()


def _resolve_agent_path(path: str) -> str:
    """Resolve the `agent` binary, checking ~/.local/bin if not on PATH."""
    import shutil

    if os.path.isabs(path) and os.path.isfile(path):
        return path

    found = shutil.which(path)
    if found:
        return found

    home = os.path.expanduser("~")
    for candidate in [
        os.path.join(home, ".local", "bin", "agent"),
        os.path.join(home, ".local", "bin", "cursor-agent"),
    ]:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate

    return path


def ask_cursor(question: str, context: str, repo_path: str = "") -> str:
    """
    Send *context* + *question* to Cursor Agent CLI and return the answer.

    Uses --mode=ask (read-only) and --print (headless) for non-interactive use.
    """
    repo_path = repo_path or REPO_PATH
    cursor_path = _resolve_agent_path(CURSOR_CLI_PATH)

    # Build the full prompt
    prompt = (
        "You are a helpful code assistant with deep expertise in software engineering.\n"
        "Answer questions based **only** on the provided codebase context.\n"
        "When you reference code, always mention the **filename** and **line numbers**.\n"
        "If the context does not contain enough information to answer, say so honestly.\n"
        "Keep answers concise but thorough. Use markdown formatting.\n\n"
        "## Codebase Context\n\n"
        f"{context}\n\n"
        "---\n\n"
        f"## Question\n{question}"
    )

    _console.print()  # blank line before answer

    try:
        cmd = [
            cursor_path,
            "--print",              # headless mode for scripts
            "--trust",              # trust workspace
            "--mode", "ask",        # read-only mode
            "--workspace", str(repo_path),
            prompt,
        ]

        # Build env — ensure ~/.local/bin is on PATH
        env = os.environ.copy()
        local_bin = os.path.expanduser("~/.local/bin")
        if local_bin not in env.get("PATH", ""):
            env["PATH"] = f"{local_bin}:{env.get('PATH', '')}"

        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )

        output = (result.stdout + result.stderr).strip()
        output = _strip_ansi(output)

        if output:
            print(output)
        print()  # newline after output

        if result.returncode != 0 and not output:
            error_msg = f"\n❌ Cursor Agent CLI error (exit {result.returncode})"
            _console.print(error_msg, style="bold red")
            return error_msg

        return output

    except FileNotFoundError:
        error_msg = (
            f"\n❌ Cursor Agent CLI not found at '{cursor_path}'.\n"
            f"Install: curl https://cursor.com/install -fsS | bash\n"
            f"Or set RI_CURSOR_CLI_PATH to the binary path."
        )
        _console.print(error_msg, style="bold red")
        return error_msg
    except subprocess.TimeoutExpired:
        error_msg = "\n❌ Cursor Agent CLI timed out (120s limit)"
        _console.print(error_msg, style="bold red")
        return error_msg
    except Exception as e:
        error_msg = f"\n❌ Cursor Agent CLI error: {e}"
        _console.print(error_msg, style="bold red")
        return error_msg


# Backward-compatible alias — old code calls ask_qwen()
def ask_qwen(question: str, context: str) -> str:
    """Backward-compatible alias → delegates to ask_cursor()."""
    return ask_cursor(question, context)


# ── Utility ───────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape codes from a string."""
    return _ANSI_RE.sub("", text)
