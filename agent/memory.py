"""
Agent memory — conversation history + persistent task log.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class AgentMemory:
    """
    Two types of memory:
    1. SHORT-TERM: conversation messages sent to Claude (managed by brain)
    2. LONG-TERM: persistent JSON log of what the agent has done (this class)
    """

    def __init__(self, workspace: str, log_filename: str = ".saniyan_log.json"):
        self.workspace = Path(workspace)
        self.log_path = self.workspace / log_filename
        self.task_log: list[dict] = self._load_log()

    def log_action(self, action: str, result: str):
        """Record an agent action to the persistent log."""
        self.task_log.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "action": action,
            "result": result[:300],  # cap to avoid bloat
        })
        self._save_log()

    def get_recent_actions(self, count: int = 10) -> list[dict]:
        """Get the most recent actions."""
        return self.task_log[-count:]

    def get_history_summary(self) -> str:
        """Format recent actions as a string for context injection."""
        if not self.task_log:
            return "(no previous actions)"
        lines = []
        for entry in self.task_log[-8:]:
            lines.append(f"  [{entry['time']}] {entry['action']}: {entry['result'][:100]}")
        return "\n".join(lines)

    def _load_log(self) -> list[dict]:
        if self.log_path.exists():
            try:
                return json.loads(self.log_path.read_text())
            except (json.JSONDecodeError, IOError):
                return []
        return []

    def _save_log(self):
        try:
            self.log_path.write_text(
                json.dumps(self.task_log[-200:], indent=2)
            )
        except IOError:
            pass

