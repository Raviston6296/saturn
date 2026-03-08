"""
Saniyan — The autonomous coding agent.

This is the agentic loop inspired by Stripe's Minions:
  1. Receive task from Cliq channel
  2. Build full workspace context
  3. Send to Claude with tools
  4. Execute tool calls → feed results back → repeat
  5. Auto-verify after every edit (run tests, self-heal)
  6. Commit + push + create PR
  7. Report back

One-shot, end-to-end. No human in the loop during execution.
"""

from __future__ import annotations

import time
from pathlib import Path

from config import settings
from agent.brain import AgentBrain
from agent.memory import AgentMemory
from agent.context import ContextBuilder
from tools.registry import TOOL_SCHEMAS, ToolExecutor


class AutonomousAgent:
    """
    The main autonomous agent. Give it a task in plain English,
    it will solve it end-to-end: read → reason → edit → test → commit → PR.
    """

    def __init__(
        self,
        workspace: str = ".",
        repo_name: str = "",
        branch_name: str = "",
        dry_run: bool = False,
    ):
        self.workspace = workspace
        self.repo_name = repo_name
        self.branch_name = branch_name

        # Core components
        self.brain = AgentBrain(tools=TOOL_SCHEMAS)
        self.executor = ToolExecutor(workspace, repo_name, dry_run)
        self.memory = AgentMemory(workspace)
        self.context_builder = ContextBuilder(workspace)

        # Tracking
        self.loop_count = 0
        self.max_loops = settings.max_loop_iterations
        self.files_changed: list[str] = []
        self.tests_passed = False
        self.pr_url: str | None = None
        self._start_time = 0.0

    def run(self, task: str) -> str:
        """
        Entry point. Give it a task in plain English.
        Returns a final summary of what was done.
        """
        self._start_time = time.time()
        print(f"\n{'━'*60}")
        print(f"🤖 SANIYAN — Autonomous Coding Agent")
        print(f"{'━'*60}")
        print(f"📋 Task: {task}")
        print(f"📁 Workspace: {self.workspace}")
        print(f"🌿 Branch: {self.branch_name or '(current)'}")
        print(f"{'━'*60}\n")

        # ── Step 1: Build full workspace context ──
        print("📸 Building workspace context snapshot...")
        context = self.context_builder.build_snapshot()

        # ── Step 2: Classify difficulty ──
        hard_problem = self.brain.classify_difficulty(task)
        if hard_problem:
            print("💡 Hard problem detected → enabling extended thinking\n")
        else:
            print("⚡ Standard task → normal mode\n")

        # ── Step 3: Compose the full prompt ──
        history = self.memory.get_history_summary()
        full_prompt = (
            f"TASK:\n{task}\n\n"
            f"WORKSPACE CONTEXT:\n{context}\n\n"
            f"PREVIOUS AGENT ACTIONS:\n{history}"
        )

        # ── Step 4: First call to Claude ──
        response = self.brain.think(full_prompt, hard_problem=hard_problem)

        # ── Step 5: Agentic loop ──
        while self.loop_count < self.max_loops:
            self.loop_count += 1
            tool_calls = self.brain.extract_tool_calls(response)

            if not tool_calls:
                # No tool calls → Claude is done (end_turn)
                print(f"\n🏁 Agent finished after {self.loop_count} iterations")
                break

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

                # Track PR URL
                if tool_name == "create_pull_request" and "OK" in result:
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

            # Next iteration: Claude decides what to do next
            response = self.brain.think("", hard_problem=False)

        else:
            print(f"\n⚠️ Max loop limit ({self.max_loops}) reached!")

        # ── Extract final summary ──
        final_summary = self.brain.extract_text(response)
        elapsed = time.time() - self._start_time

        print(f"\n{'━'*60}")
        print(f"✅ SANIYAN — Task Complete")
        print(f"⏱️  Duration: {elapsed:.1f}s")
        print(f"🔁 Loop iterations: {self.loop_count}")
        print(f"📁 Files changed: {len(self.files_changed)}")
        print(f"🧪 Tests passed: {'✅' if self.tests_passed else '❌ (or not run)'}")
        if self.pr_url:
            print(f"🔗 PR: {self.pr_url}")
        print(f"{'━'*60}\n")

        return final_summary

    def _auto_verify(self):
        """
        After file edits, automatically run tests.
        If tests fail, feed the failure back to Claude so it can self-heal.
        """
        print("  🧪 Auto-verifying (running tests)...")

        test_result = self.context_builder.get_test_status()

        if not test_result or test_result == "(no test runner detected)":
            print("  ℹ️  No test runner detected — skipping verification")
            return

        # Check for failures
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
        """Create a short summary of tool inputs for logging."""
        parts = []
        for key, value in inputs.items():
            if isinstance(value, str) and len(value) > 50:
                parts.append(f"{key}='{value[:47]}...'")
            else:
                parts.append(f"{key}={value!r}")
        return ", ".join(parts)

