"""
Saturn — The autonomous coding agent.

This is the agentic loop inspired by Stripe's Minions:
  1. Receive task from Cliq channel
  2. Build full context (repo-level + worktree-level)
  3. Send to Claude with tools
  4. Execute tool calls → feed results back → repeat
  5. Auto-verify after every edit (run tests, self-heal)
  6. Commit + push + create PR
  7. Report back

Each task runs in its own git worktree — lightweight, fast, isolated.
The repo stays persistent so Saturn learns over time.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from config import settings
from agent.brain import AgentBrain
from agent.memory import AgentMemory
from agent.context import ContextBuilder
from tools.registry import TOOL_SCHEMAS, ToolExecutor

if TYPE_CHECKING:
    from dispatcher.workspace import RepoManager


class AutonomousAgent:
    """
    The main autonomous agent. Give it a task in plain English,
    it will solve it end-to-end: read → reason → edit → test → commit → PR.

    Runs inside a git worktree — one branch per task, fully isolated.
    """

    def __init__(
        self,
        workspace: str = ".",
        repo_name: str = "",
        branch_name: str = "",
        dry_run: bool = False,
        repo_manager: "RepoManager | None" = None,
    ):
        self.workspace = workspace
        self.repo_name = repo_name
        self.branch_name = branch_name
        self.repo_manager = repo_manager

        # Core components
        self.brain = AgentBrain(tools=TOOL_SCHEMAS)
        self.executor = ToolExecutor(workspace, repo_name, dry_run)
        self.memory = AgentMemory(
            workspace=workspace,
            repo_memory_dir=str(repo_manager.repo_path) if repo_manager else None,
        )
        self.context_builder = ContextBuilder(
            workspace=workspace,
            repo_manager=repo_manager,
        )

        # Tracking
        self.loop_count = 0
        self.max_loops = settings.max_loop_iterations
        self.files_changed: list[str] = []
        self.tests_passed = False
        self.pr_url: str | None = None
        self._start_time = 0.0
        self._last_tool_sig: str = ""
        self._repeat_count: int = 0
        self._total_nudges: int = 0

    def run(self, task: str) -> str:
        """
        Entry point. Give it a task in plain English.
        Returns a final summary of what was done.
        """
        self._start_time = time.time()
        print(f"\n{'━'*60}")
        print(f"🪐 SATURN — Autonomous Coding Agent")
        print(f"{'━'*60}")
        print(f"📋 Task: {task}")
        print(f"📁 Worktree: {self.workspace}")
        print(f"🌿 Branch: {self.branch_name or '(current)'}")
        print(f"{'━'*60}\n")

        # ── Step 1: Build full context (repo + worktree) ──
        print("📸 Building context snapshot (repo knowledge + worktree state)...")
        context = self.context_builder.build_snapshot()

        # ── Step 2: Classify difficulty ──
        hard_problem = self.brain.classify_difficulty(task)
        if hard_problem:
            print("💡 Hard problem detected → enabling extended thinking\n")
        else:
            print("⚡ Standard task → normal mode\n")

        # ── Step 3: Compose the full prompt ──
        # Keep it short — small models get overwhelmed by huge context.
        # The LLM can explore the workspace itself via tools.
        full_prompt = (
            f"TASK: {task}\n\n"
            f"The workspace is at: {self.workspace}\n"
            f"Start by calling list_directory to explore, then complete the task using tools."
        )

        # ── Step 4: First call to LLM ──
        print("🧠 Calling LLM...")
        response = self.brain.think(full_prompt, hard_problem=hard_problem)

        # Debug: show what we got back
        text_preview = self.brain.extract_text(response)
        tool_calls_preview = self.brain.extract_tool_calls(response)
        print(f"  📨 LLM response: {len(tool_calls_preview)} tool calls, text={text_preview[:150] if text_preview else '(none)'}...")

        # ── Step 5: Agentic loop ──
        while self.loop_count < self.max_loops:
            self.loop_count += 1
            tool_calls = self.brain.extract_tool_calls(response)

            if not tool_calls:
                print(f"\n🏁 Agent finished after {self.loop_count} iterations")
                break

            # ── Repetition detection (small models get stuck in loops) ──
            tool_sig = "|".join(f"{c.name}:{sorted(c.input.items())}" for c in tool_calls)
            if tool_sig == self._last_tool_sig:
                self._repeat_count += 1
            else:
                self._repeat_count = 0
                self._last_tool_sig = tool_sig

            if self._repeat_count >= 2:
                self._total_nudges += 1
                print(f"  ⚠️ Repetition detected ({self._repeat_count + 1}x same call) — nudging LLM (nudge #{self._total_nudges})")
                self._repeat_count = 0
                self._last_tool_sig = ""

                # After 2 nudges the model is hopelessly stuck — force-break
                if self._total_nudges >= 2:
                    print(f"  🛑 Model stuck in loop after {self._total_nudges} nudges — force-breaking")
                    break

                # Context-aware nudge message
                repeated_names = {c.name for c in tool_calls}
                if repeated_names & {"create_file", "edit_file"}:
                    nudge_msg = (
                        "⚠️ The file has already been created/edited successfully. "
                        "Do NOT call create_file or edit_file again. "
                        "The task is DONE. Stop calling tools and respond with a summary of what you did."
                    )
                else:
                    nudge_msg = (
                        "⚠️ You already called this tool with the same arguments. "
                        "The result has not changed. STOP repeating and move to the NEXT step. "
                        "Use create_file or edit_file to make the changes required by the task."
                    )

                nudge_results = []
                for call in tool_calls:
                    nudge_results.append({
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": nudge_msg,
                    })
                self.brain.inject_tool_results(nudge_results)
                response = self.brain.think("", hard_problem=False)
                continue

            # Execute all tool calls
            tool_results = []
            has_edits = False

            for call in tool_calls:
                tool_name = call.name
                tool_input = call.input
                print(f"  🔧 [{self.loop_count:02d}] {tool_name}({self._summarize_input(tool_input)})")

                result = self.executor.execute(tool_name, tool_input)
                self.memory.log_action(tool_name, result)

                # Track file changes
                if tool_name in ("edit_file", "create_file") and "OK" in result:
                    filepath = tool_input.get("path", "")
                    if filepath and filepath not in self.files_changed:
                        self.files_changed.append(filepath)
                    has_edits = True

                # Track MR URL
                if tool_name == "create_merge_request" and "OK" in result:
                    for line in result.split("\n"):
                        if "→" in line and "http" in line:
                            self.pr_url = line.split("→")[-1].strip()

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": result,
                })

            # Feed results back to Claude
            self.brain.inject_tool_results(tool_results)

            # ── Auto-verify after edits ──
            if has_edits:
                self._auto_verify()

            # Next iteration
            response = self.brain.think("", hard_problem=False)

        else:
            print(f"\n⚠️ Max loop limit ({self.max_loops}) reached!")

        # ── Extract final summary ──
        final_summary = self.brain.extract_text(response)

        # ── Step 6: Auto-finalize (commit → push → MR) ──
        print(f"\n📊 Agent loop done. files_changed={len(self.files_changed)}, pr_url={self.pr_url}")
        self._auto_finalize(task, final_summary)

        elapsed = time.time() - self._start_time

        # ── Save to persistent repo memory ──
        self.memory.save_task_summary(
            task_id=self.branch_name or "unknown",
            description=task,
            summary=final_summary[:300],
            pr_url=self.pr_url or "",
        )

        print(f"\n{'━'*60}")
        print(f"✅ SATURN — Task Complete")
        print(f"⏱️  Duration: {elapsed:.1f}s")
        print(f"🔁 Loop iterations: {self.loop_count}")
        print(f"📁 Files changed: {len(self.files_changed)}")
        print(f"🧪 Tests passed: {'✅' if self.tests_passed else '❌ (or not run)'}")
        if self.pr_url:
            print(f"🔗 PR: {self.pr_url}")
        print(f"{'━'*60}\n")

        return final_summary

    def _auto_finalize(self, task: str, summary: str):
        """
        Auto commit → push → create MR after the agent finishes.
        Checks git status directly to detect changes (not just self.files_changed).
        Skips if MR was already created by the LLM during the agentic loop.
        """
        if self.pr_url:
            print("\n📦 MR already created by agent — skipping auto-finalize")
            return

        print("\n📦 Auto-finalizing: commit → push → MR...")

        # ── Check for actual changes via git status ──
        status_result = self.executor.execute("git_status", {})
        print(f"  📋 Git status: {status_result.strip()[:200] if status_result else '(clean)'}")

        if not status_result or not status_result.strip() or status_result.strip() == "(empty)":
            print("  ℹ️  No changes detected — skipping finalize")
            return

        # ── Commit ──
        commit_msg = self._generate_commit_message(task)
        print(f"  💾 Committing: {commit_msg}")
        commit_result = self.executor.execute("git_commit", {"message": commit_msg})
        print(f"  {commit_result[:200] if commit_result else '(empty)'}")

        if "NOTHING TO COMMIT" in commit_result:
            print("  ℹ️  Nothing to commit — skipping push & MR")
            return

        # ── Push ──
        print("  🚀 Pushing to origin...")
        push_result = self.executor.execute("git_push", {})
        print(f"  {push_result[:200] if push_result else '(empty)'}")

        # git push writes normal output to stderr, so check for actual failure patterns
        push_failed = any(fail in push_result for fail in [
            "ERROR (exit", "fatal:", "rejected", "failed to push",
        ]) if push_result else False

        if push_failed:
            print(f"  ❌ Push failed — skipping MR")
            return

        # ── Create MR ──
        mr_title = self._generate_mr_title(task)
        mr_body = self._generate_mr_body(task, summary)
        print(f"  📝 Creating MR: {mr_title}")

        try:
            mr_result = self.executor.execute("create_merge_request", {
                "title": mr_title,
                "body": mr_body,
            })
            print(f"  {mr_result}")

            # Extract MR URL
            if "OK" in mr_result:
                for line in mr_result.split("\n"):
                    if "→" in line and "http" in line:
                        self.pr_url = line.split("→")[-1].strip()
                print(f"  ✅ MR created: {self.pr_url}")
            else:
                print(f"  ⚠️  MR creation result: {mr_result}")
        except Exception as e:
            print(f"  ⚠️  MR creation error: {e}")

    def _generate_commit_message(self, task: str) -> str:
        """Generate a conventional commit message from the task."""
        task_lower = task.lower()
        if any(w in task_lower for w in ["fix", "bug", "broken", "error", "crash"]):
            prefix = "fix"
        elif any(w in task_lower for w in ["refactor", "clean", "reorganize"]):
            prefix = "refactor"
        elif any(w in task_lower for w in ["test", "spec", "coverage"]):
            prefix = "test"
        elif any(w in task_lower for w in ["doc", "readme", "comment"]):
            prefix = "docs"
        else:
            prefix = "feat"

        # Truncate task to fit commit message
        short_desc = task[:72].strip()
        if len(task) > 72:
            short_desc = short_desc.rsplit(" ", 1)[0] + "..."
        return f"{prefix}: {short_desc}"

    def _generate_mr_title(self, task: str) -> str:
        """Generate a MR title from the task."""
        title = task[:100].strip()
        if len(task) > 100:
            title = title.rsplit(" ", 1)[0] + "..."
        return f"[Saturn] {title}"

    def _generate_mr_body(self, task: str, summary: str) -> str:
        """Generate a MR description."""
        files_list = "\n".join(f"- `{f}`" for f in self.files_changed) if self.files_changed else "- (none)"
        return (
            f"## 🪐 Saturn — Autonomous Agent MR\n\n"
            f"### Task\n{task}\n\n"
            f"### Summary\n{summary[:500] if summary else '(no summary)'}\n\n"
            f"### Files Changed\n{files_list}\n\n"
            f"### Details\n"
            f"- **Branch:** `{self.branch_name}`\n"
            f"- **Loop iterations:** {self.loop_count}\n"
            f"- **Tests passed:** {'✅' if self.tests_passed else '❌ (or not run)'}\n\n"
            f"---\n*This MR was created automatically by Saturn.*"
        )

    def _auto_verify(self):
        """After file edits, automatically run tests and self-heal."""
        print("  🧪 Auto-verifying (running tests)...")

        test_result = self.context_builder.get_test_status()

        if not test_result or test_result == "(no test runner detected)":
            print("  ℹ️  No test runner detected — skipping verification")
            return

        failure_indicators = ["FAIL", "FAILED", "ERROR", "error", "AssertionError", "Exception"]
        has_failure = any(indicator in test_result for indicator in failure_indicators)

        if has_failure:
            print("  ⚠️  Tests failing → injecting failure for self-heal")
            self.tests_passed = False
            self.brain.inject_tool_results([{
                "type": "tool_result",
                "tool_use_id": "auto_verify",
                "content": (
                    "⚠️ AUTO-VERIFY FAILED — Tests are failing after your edits:\n\n"
                    f"{test_result}\n\n"
                    "Please fix these failures before proceeding. "
                    "Read the failing test, understand the error, and fix your code."
                ),
            }])
        else:
            print("  ✅ Tests passing")
            self.tests_passed = True

    def _summarize_input(self, inputs: dict) -> str:
        parts = []
        for key, value in inputs.items():
            if isinstance(value, str) and len(value) > 50:
                parts.append(f"{key}='{value[:47]}...'")
            else:
                parts.append(f"{key}={value!r}")
        return ", ".join(parts)

