"""
Agent memory — repo-level persistent memory + per-task action log.

Since Saturn lives with one repo, we keep a persistent memory file
in the bare clone directory that survives across tasks. This gives
the agent memory of past tasks, common patterns, and what it learned.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


class AgentMemory:
    """
    Two tiers of memory:
    1. TASK LOG (ephemeral) — actions taken in the current task
    2. REPO MEMORY (persistent) — survives across all tasks, stored in bare clone dir
    """

    def __init__(
        self,
        workspace: str,
        repo_memory_dir: str | None = None,
        task_log_filename: str = ".saturn_task_log.json",
        repo_memory_filename: str = "saturn_memory.json",
    ):
        self.workspace = Path(workspace)

        # Per-task log (lives in worktree, gets cleaned up)
        self.task_log_path = self.workspace / task_log_filename
        self.task_log: list[dict] = []

        # Repo-level memory (lives in bare clone dir, persists forever)
        if repo_memory_dir:
            self.repo_memory_path = Path(repo_memory_dir) / repo_memory_filename
        else:
            self.repo_memory_path = None
        self.repo_memory: dict = self._load_repo_memory()

    # ━━━ Task-level log ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def log_action(self, action: str, result: str):
        """Record an agent action in the current task."""
        self.task_log.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "action": action,
            "result": result[:300],
        })

    def get_recent_actions(self, count: int = 10) -> list[dict]:
        return self.task_log[-count:]

    # ━━━ Repo-level persistent memory ━━━━━━━━━━━━━━━━━━━━━━━━━━

    def save_task_summary(self, task_id: str, description: str, summary: str, pr_url: str = ""):
        """Save a task completion record to persistent repo memory."""
        if not self.repo_memory_path:
            return

        past_tasks = self.repo_memory.get("past_tasks", [])
        past_tasks.append({
            "task_id": task_id,
            "date": datetime.now().isoformat(),
            "description": description[:200],
            "summary": summary[:300],
            "pr_url": pr_url,
        })

        # Keep last 50 tasks
        self.repo_memory["past_tasks"] = past_tasks[-50:]
        self._save_repo_memory()

    def get_past_tasks(self, count: int = 10) -> list[dict]:
        """Get past task summaries from repo memory."""
        return self.repo_memory.get("past_tasks", [])[-count:]

    def add_knowledge(self, key: str, value: str):
        """Store a piece of learned knowledge about the repo."""
        knowledge = self.repo_memory.get("knowledge", {})
        knowledge[key] = {
            "value": value[:500],
            "updated": datetime.now().isoformat(),
        }
        self.repo_memory["knowledge"] = knowledge
        self._save_repo_memory()

    # ━━━ Context injection ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def get_history_summary(self) -> str:
        """
        Build a combined summary of:
        1. Past tasks Saturn has done on this repo (persistent)
        2. Current task actions (ephemeral)
        """
        lines = []

        # Repo-level past tasks
        past = self.get_past_tasks(5)
        if past:
            lines.append("── Past tasks on this repo ──")
            for entry in past:
                lines.append(
                    f"  [{entry.get('date', '?')[:10]}] {entry.get('description', '?')[:80]}"
                    f" → {entry.get('summary', '?')[:60]}"
                )
            lines.append("")

        # Repo-level knowledge
        knowledge = self.repo_memory.get("knowledge", {})
        if knowledge:
            lines.append("── Learned patterns ──")
            for key, val in list(knowledge.items())[-5:]:
                lines.append(f"  {key}: {val.get('value', '')[:80]}")
            lines.append("")

        # Current task actions
        if self.task_log:
            lines.append("── Current task actions ──")
            for entry in self.task_log[-8:]:
                lines.append(f"  [{entry['time']}] {entry['action']}: {entry['result'][:100]}")
        else:
            lines.append("(no actions yet in current task)")

        return "\n".join(lines) if lines else "(no previous history)"

    # ━━━ Internal ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def _load_repo_memory(self) -> dict:
        if self.repo_memory_path and self.repo_memory_path.exists():
            try:
                return json.loads(self.repo_memory_path.read_text())
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save_repo_memory(self):
        if not self.repo_memory_path:
            return
        try:
            self.repo_memory_path.parent.mkdir(parents=True, exist_ok=True)
            self.repo_memory_path.write_text(
                json.dumps(self.repo_memory, indent=2)
            )
        except IOError:
            pass

