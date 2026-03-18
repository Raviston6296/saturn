"""
Saturn — The autonomous coding agent.

When LLM_PROVIDER=cursor (default), all coding work is delegated to
the Cursor Agent CLI (`agent`). When LLM_PROVIDER=goose, work is delegated
to the Goose AI coding agent. Saturn is a thin orchestrator:
  1. Receive task from Cliq channel
  2. Create git worktree for isolation
  3. Invoke the coding agent (Cursor or Goose) to do the coding
  4. Run deterministic gates (.saturn/gates.yaml) — risk check + validation
  5. Auto-verify (run tests, self-heal via the coding agent)
  6. Commit + push + create MR
  7. Report back to Cliq

Hybrid mode (LLM_PROVIDER=cursor+goose):
  Cursor handles the LLM coding work (reads/edits files).
  Goose orchestrates validation via the Saturn MCP extension:
    - Pre-flight context scan (project structure + DPAAS env)
    - compile_quick() after each Cursor coding round (Tier 1)
    - run_module_tests() before finishing (Tier 2)
    - find_similar_code / get_test_template for context injection
    - Gate fix loop: Cursor fixes code, Goose validates the fix
  This gives the best of both:
    Cursor's powerful code generation + Goose's deep ZDPAS tooling

Legacy mode (LLM_PROVIDER=ollama/anthropic) keeps the original agentic
loop with brain.py + tool schemas for backward compatibility.

Each task runs in its own git worktree — lightweight, fast, isolated.
The repo stays persistent so Saturn learns over time.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from config import settings
from agent.cursor_cli import CursorCLI, CursorResult
from agent.memory import AgentMemory
from agent.context import ContextBuilder
from gates import GatePipeline, GatePipelineResult
from gates.incremental import ZDPAS_MODULE_MAPPING
from tools.registry import TOOL_SCHEMAS, ToolExecutor

if TYPE_CHECKING:
    from dispatcher.workspace import RepoManager

# Aliases the unit-test gate accepts → canonical module name for SATURN_TEST_MODULES
_ZDPAS_MODULE_ALIASES = {
    "transforms": "transformer",
    "io": "dataframe",
    "utils": "util",
    "csvreader": "dataframe",
    "excelio": "dataframe",
    "jsonio": "dataframe",
    "xmlio": "dataframe",
    "dedup": "transformer",
    "deduplicate": "transformer",
    "fill": "transformer",
    "fillcells": "transformer",
}


def _parse_llm_module_list(reply: str, known: set[str]) -> set[str]:
    """
    Parse LLM reply into a set of canonical ZDPAS module names.
    Handles comma/newline separation, markdown, and common aliases.
    """
    import re
    # Strip markdown code blocks and extra whitespace
    text = re.sub(r"```[\w]*\n?", "", reply).strip()
    tokens = re.split(r"[\s,;]+", text)
    result: set[str] = set()
    for t in tokens:
        t = t.strip().lower()
        if not t:
            continue
        canonical = _ZDPAS_MODULE_ALIASES.get(t, t)
        if canonical in known:
            result.add(canonical)
    return result


def _get_brain(tools):
    """Lazy-import AgentBrain only when legacy LLM mode is used."""
    from agent.brain import AgentBrain
    return AgentBrain(tools=tools)


class AutonomousAgent:
    """
    The main autonomous agent. Give it a task in plain English,
    it will solve it end-to-end: read → reason → edit → test → commit → PR.

    Supported coding engines (LLM_PROVIDER):
      cursor    — Cursor Agent CLI (default)
      goose     — Goose AI coding agent (open-source, by Block)
      ollama    — Legacy Ollama LLM loop
      anthropic — Legacy Anthropic LLM loop

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

        provider = settings.llm_provider.lower()
        self.use_cursor = provider == "cursor"
        self.use_goose = provider == "goose"
        # Hybrid mode: Cursor does the coding, Goose orchestrates validation
        self.use_hybrid = provider == "cursor+goose"

        # Core components — Cursor CLI, Goose CLI, or legacy brain
        if self.use_cursor:
            self.cursor = CursorCLI()
            self.goose = None
            self.brain = None
        elif self.use_goose:
            from agent.goose_agent import GooseAgent
            self.goose = GooseAgent(
                workspace=workspace,
                branch_name=branch_name,
            )
            self.cursor = None
            self.brain = None
        elif self.use_hybrid:
            # Both engines: Cursor for coding, Goose for validation/orchestration
            from agent.goose_agent import GooseAgent
            self.cursor = CursorCLI()
            self.goose = GooseAgent(
                workspace=workspace,
                branch_name=branch_name,
            )
            self.brain = None
        else:
            self.cursor = None
            self.goose = None
            self.brain = _get_brain(tools=TOOL_SCHEMAS)

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
        self.gates_result: GatePipelineResult | None = None
        self.pr_url: str | None = None
        self._structured_summary = None  # StructuredSummary from Goose
        self._start_time = 0.0
        self._last_tool_sig: str = ""
        self._repeat_count: int = 0
        self._total_nudges: int = 0
        self._file_edit_count: dict[str, int] = {}  # path → edit count

    def run(self, task: str) -> str:
        """
        Entry point. Give it a task in plain English.
        Returns a final summary of what was done.

        Dispatch order:
          LLM_PROVIDER=cursor  → Cursor Agent CLI
          LLM_PROVIDER=goose   → Goose CLI
          otherwise            → legacy LLM brain (Ollama / Anthropic)
        """
        self._start_time = time.time()
        print(f"\n{'━'*60}")
        print(f"🪐 SATURN — Autonomous Coding Agent")
        print(f"{'━'*60}")
        print(f"📋 Task: {task}")
        print(f"📁 Worktree: {self.workspace}")
        print(f"🌿 Branch: {self.branch_name or '(current)'}")
        if self.use_cursor:
            engine = "Cursor CLI"
        elif self.use_goose:
            engine = "Goose CLI"
        elif self.use_hybrid:
            engine = "Cursor (coding) + Goose (orchestration)"
        else:
            engine = settings.llm_provider
        print(f"🔧 Engine: {engine}")
        print(f"{'━'*60}\n")

        if self.use_cursor:
            final_summary = self._run_with_cursor(task)
        elif self.use_goose:
            final_summary = self._run_with_goose(task)
        elif self.use_hybrid:
            final_summary = self._run_with_hybrid(task)
        else:
            final_summary = self._run_with_legacy_brain(task)

        # ── Deterministic gates (risk check + validation) ──
        print(f"\n📊 Agent done. files_changed={len(self.files_changed)}, pr_url={self.pr_url}")
        gates_ok = self._run_gates(task)

        # ── Auto-finalize (commit → push → MR) — only if gates pass ──
        if gates_ok:
            self._auto_finalize(task, final_summary)
        else:
            print("\n🛑 Gates failed — skipping auto-finalize (no MR will be created)")

        elapsed = time.time() - self._start_time

        # ── Save to persistent repo memory ──
        self.memory.save_task_summary(
            task_id=self.branch_name or "unknown",
            description=task,
            summary=final_summary[:300],
            pr_url=self.pr_url or "",
        )

        # ── Cleanup Goose session (frees disk space) ──
        if (self.use_goose or self.use_hybrid) and self.goose:
            self.goose.cleanup_session()

        gates_icon = "✅" if (not self.gates_result or self.gates_result.passed) else "❌"

        print(f"\n{'━'*60}")
        print(f"✅ SATURN — Task Complete")
        print(f"⏱️  Duration: {elapsed:.1f}s")
        print(f"🔁 Loop iterations: {self.loop_count}")
        print(f"📁 Files changed: {len(self.files_changed)}")
        print(f"🧪 Tests passed: {'✅' if self.tests_passed else '❌ (or not run)'}")
        print(f"🚧 Gates: {gates_icon}")
        if self.pr_url:
            print(f"🔗 PR: {self.pr_url}")
        print(f"{'━'*60}\n")

        return final_summary

    # ── Cursor CLI mode ───────────────────────────────────────────

    def _run_with_cursor(self, task: str) -> str:
        """
        Delegate the entire coding task to Cursor CLI.

        Cursor handles:
          - Reading/exploring files
          - Making code edits
          - Understanding codebase context

        Saturn handles (after Cursor finishes):
          - Git commit + push
          - MR creation
          - Reporting to Cliq
        """
        # Build the full prompt with workspace context for Cursor
        prompt = self._build_cursor_prompt(task)

        print("🖥️  Delegating task to Cursor CLI...")

        # Run Cursor CLI — it does ALL the coding work
        result = self.cursor.run(
            prompt=prompt,
            workspace=self.workspace,
        )

        self.loop_count = 1  # Cursor runs as a single invocation

        if not result.success:
            print(f"  ❌ Cursor CLI failed: {result.error}")
            print(f"  📤 Output: {result.output[:500]}")
            return f"Cursor CLI failed: {result.error}\n\nOutput:\n{result.output[:500]}"

        # Track changed files from Cursor's work
        self.files_changed = result.files_changed
        print(f"  ✅ Cursor CLI finished — {len(self.files_changed)} files changed")

        if self.files_changed:
            for f in self.files_changed[:20]:
                print(f"    📝 {f}")

        # Run tests to verify Cursor's changes
        self._auto_verify_cursor()

        return result.summary or "Cursor CLI completed the task."

    # ── Goose CLI mode ────────────────────────────────────────────

    def _run_with_goose(self, task: str) -> str:
        """
        Delegate the entire coding task to GooseAgent (enhanced Goose flow).

        GooseAgent provides:
          - Pre-flight context scan (project structure + DPAAS env check)
          - Named sessions (Goose keeps context across gate fix retries)
          - Real-time streaming output
          - Rich ZDPAS context injection
          - Saturn MCP tools: find_similar_code, get_test_template,
            compile_quick, run_module_tests, sync_resources, …
          - Structured error analysis for fix prompts

        Saturn handles (after Goose finishes):
          - Deterministic gates — risk check + Tier 1 static validation
            (Tier 2 unit tests already run by Goose via MCP)
          - Git commit + push
          - MR creation
          - Reporting to Cliq
        """
        print("🪿  Delegating task to GooseAgent (enhanced Goose flow)...")

        # Pre-flight: gather project context before Goose starts
        preflight_summary = self.goose.pre_flight()
        print(preflight_summary)

        result = self.goose.run(
            task=task,
            files_changed=self.files_changed,
        )

        self.loop_count = 1

        # Always capture files changed — even on timeout/failure Goose may
        # have made valid edits before it was killed.
        if result.files_changed:
            self.files_changed = result.files_changed

        if not result.success:
            if self.files_changed and "timed out" in (result.error or result.output):
                # Partial success: Goose was actively working, made changes,
                # but ran out of time.  Proceed to gates — the changes may be
                # valid and just need the final compile/test verification.
                print(f"  ⏳ GooseAgent timed out but made {len(self.files_changed)} file change(s) — treating as partial success")
                for f in self.files_changed[:20]:
                    print(f"    📝 {f}")
                print("  🚧 Proceeding to deterministic gates for validation...")
                self._structured_summary = result.structured_summary
                return result.summary or f"GooseAgent timed out after making {len(self.files_changed)} change(s). Proceeding to gate validation."
            else:
                print(f"  ❌ GooseAgent failed: {result.error}")
                print(f"  📤 Output: {result.output[:500]}")
                print(f"  📁 Files changed before failure: {len(self.files_changed)}")
                return f"GooseAgent failed: {result.error}\n\nOutput:\n{result.output[:500]}"

        print(f"  ✅ GooseAgent finished — {len(self.files_changed)} files changed")

        if self.files_changed:
            for f in self.files_changed[:20]:
                print(f"    📝 {f}")

        self._structured_summary = result.structured_summary
        return result.summary or "GooseAgent completed the task."

    # ── Hybrid mode (Cursor coding + Goose orchestration) ────────

    def _run_with_hybrid(self, task: str) -> str:
        """
        Hybrid mode: Cursor handles LLM coding, Goose orchestrates validation.

        Flow:
          1. Goose pre-flight  — project context + DPAAS env check
          2. Goose injects rich ZDPAS context into the Cursor prompt
             (find_similar_code, get_test_template, affected modules)
          3. Cursor runs the coding task using its powerful LLM
          4. Goose validates Cursor's output via MCP:
             - compile_quick() on changed files (Tier 1)
             - run_module_tests() for affected modules (Tier 2)
          5. If validation fails → Goose provides structured analysis,
             Cursor retries with the enriched error context
          6. Saturn runs final Tier-1 gate pipeline (Goose already handled Tier 2)

        Advantages over pure Cursor or pure Goose:
          - Cursor's LLM is stronger at complex code generation
          - Goose's MCP tools give immediate compile/test feedback
          - Gate retries combine Cursor's fix quality + Goose's validation speed
          - Named Goose session keeps validation context across retries
        """
        print("🖥️🪿 Hybrid mode: Cursor coding + Goose orchestration")

        # Step 1: Goose pre-flight — populates project structure cache
        preflight_summary = self.goose.pre_flight()
        print(preflight_summary)

        # Step 2: Enrich the Cursor prompt with ZDPAS context from Goose MCP
        cursor_prompt = self._build_hybrid_cursor_prompt(task)

        # Step 3: Cursor does the actual coding
        print("\n  🖥️  Cursor coding phase...")
        cursor_result = self.cursor.run(
            prompt=cursor_prompt,
            workspace=self.workspace,
        )

        self.loop_count = 1

        if cursor_result.files_changed:
            self.files_changed = cursor_result.files_changed
            print(f"  ✅ Cursor finished — {len(self.files_changed)} files changed")
            for f in self.files_changed[:20]:
                print(f"    📝 {f}")
        else:
            print(f"  ℹ️  Cursor finished — no file changes detected")

        if not cursor_result.success and not cursor_result.files_changed:
            print(f"  ❌ Cursor failed: {cursor_result.error}")
            return f"Cursor failed: {cursor_result.error}\n\nOutput:\n{cursor_result.output[:500]}"

        # Step 4: Goose validates Cursor's changes via MCP (Tier 1 + Tier 2)
        if self.files_changed:
            print("\n  🪿  Goose validation phase (compile_quick + run_module_tests)...")
            goose_result = self.goose.run(
                task=(
                    f"Cursor just completed this task: {task[:200]}\n\n"
                    f"Changed files: {', '.join(self.files_changed[:10])}\n\n"
                    "Your job: validate the changes using Saturn MCP tools.\n"
                    "1. Call compile_quick() on all changed Scala/Java files\n"
                    "2. If compile passes, call run_module_tests() for affected modules\n"
                    "3. If any issues found, fix them directly\n"
                    "4. Report the final validation result\n"
                    "Do NOT re-implement the task — only validate and fix issues."
                ),
                files_changed=self.files_changed,
            )

            if goose_result.files_changed:
                # Goose may have fixed compilation/test issues
                self.files_changed.extend(goose_result.files_changed)
                self.files_changed = list(dict.fromkeys(self.files_changed))
                print(f"  🪿  Goose made {len(goose_result.files_changed)} additional fix(es)")

            print(f"  🪿  Goose validation done — success={goose_result.success}")
        else:
            goose_result = None

        # Build final summary combining both engines
        summary_parts = []
        if cursor_result.success or cursor_result.files_changed:
            summary_parts.append(cursor_result.summary or "Cursor completed the coding task.")
        if goose_result and (goose_result.success or goose_result.files_changed):
            summary_parts.append(f"Goose validation: {goose_result.summary[:200]}")

        return "\n\n".join(summary_parts) or "Hybrid agent completed the task."

    def _build_hybrid_cursor_prompt(self, task: str) -> str:
        """
        Build a Cursor prompt enriched with ZDPAS context from Goose MCP tools.

        Injects project structure and similar-code examples so Cursor's LLM
        can generate code that matches the existing patterns — without Cursor
        having to discover them itself.
        """
        # Get project context from Goose's cached pre-flight data
        project_context = ""
        if self.goose and self.goose._project_structure:
            project_context = f"\n## Project Structure\n\n{self.goose._project_structure}\n"

        # Standard Cursor prompt sections
        base_prompt = self._build_cursor_prompt(task)

        if project_context:
            # Insert ZDPAS context after the task description
            return base_prompt + (
                "\n\n## ZDPAS Context (pre-loaded by Goose)\n"
                + project_context
                + "\n## Instructions\n"
                "After making your code changes, Goose will automatically validate\n"
                "them using compile_quick and run_module_tests via the Saturn MCP\n"
                "extension. Focus on writing correct code — Goose handles validation.\n"
                "Do NOT run compilation or tests manually.\n"
            )
        return base_prompt
        """
        Build a prompt for Cursor CLI or Goose CLI (GooseAgent builds its own rich prompt).

        For GooseAgent, this is only used as a fallback; the enhanced flow uses
        GooseAgent._build_rich_prompt() directly.
        """
        return self._build_cursor_prompt(task)

    def _build_cursor_prompt(self, task: str) -> str:
        """
        Build a comprehensive prompt for Cursor CLI.

        Includes task description + repo context so Cursor
        knows what it's working with.
        """
        parts = [
            f"# Task\n\n{task}\n",
            f"# Workspace\n\nThe project is at: {self.workspace}\n",
        ]

        # Add repo context (past tasks, branch info)
        past_tasks = self.memory.get_past_tasks(5)
        if past_tasks:
            history = "\n".join(
                f"  - [{t['date'][:10]}] {t['description'][:80]}"
                for t in past_tasks
            )
            parts.append(f"# Previous Tasks on This Repo\n\n{history}\n")

        # Add specific instructions
        parts.append(
            "# Instructions\n\n"
            "- Read the relevant files first before making changes\n"
            "- Match the existing code style\n"
            "- Make all necessary file edits to complete the task\n"
            "- If tests exist, ensure they still pass\n"
            "- Do NOT commit or push — that will be handled automatically\n"
        )

        return "\n".join(parts)

    def _auto_verify_cursor(self):
        """Run tests after Cursor's changes to verify correctness."""
        if not self.files_changed:
            return

        print("  🧪 Auto-verifying Cursor's changes (running tests)...")
        test_result = self.context_builder.get_test_status()

        if not test_result or test_result == "(no test runner detected)":
            print("  ℹ️  No test runner detected — skipping verification")
            return

        failure_indicators = ["FAIL", "FAILED", "ERROR", "error", "AssertionError", "Exception"]
        has_failure = any(indicator in test_result for indicator in failure_indicators)

        if has_failure:
            print("  ⚠️  Tests failing after Cursor's changes — running Cursor again to fix...")
            self.tests_passed = False

            # Give Cursor another shot to fix the test failures
            fix_prompt = (
                f"The tests are failing after the previous changes. "
                f"Please fix the code to make the tests pass.\n\n"
                f"Test output:\n```\n{test_result[:3000]}\n```"
            )
            fix_result = self.cursor.run(
                prompt=fix_prompt,
                workspace=self.workspace,
            )

            if fix_result.files_changed:
                self.files_changed.extend(fix_result.files_changed)
                # Deduplicate
                self.files_changed = list(dict.fromkeys(self.files_changed))

            # Re-check tests
            retest = self.context_builder.get_test_status()
            if retest and not any(ind in retest for ind in failure_indicators):
                print("  ✅ Tests now passing after fix")
                self.tests_passed = True
            else:
                print("  ❌ Tests still failing after fix attempt")
                self.tests_passed = False
        else:
            print("  ✅ Tests passing")
            self.tests_passed = True

    # ── Pre-search helper for smaller models ──────────────────────

    def _pre_search_files(self, task: str) -> str:
        """
        Extract potential file names from the task and search for them.
        Returns search results to include in the prompt, helping smaller models.
        """
        import re
        import subprocess

        # Extract potential file names from task (e.g., "ZDAppendSuites.scala" or "ZDAppendSuites")
        # Look for CamelCase words that might be class/file names
        potential_names = re.findall(r'\b([A-Z][a-zA-Z0-9]+(?:Suite|Test|Spec|s)?)\b', task)

        # Also look for explicit file names with extensions
        file_patterns = re.findall(r'\b(\w+\.\w+)\b', task)
        potential_names.extend(file_patterns)

        if not potential_names:
            return ""

        print(f"  🔍 Pre-searching for: {potential_names}")

        results = []
        for name in potential_names[:3]:  # Limit to first 3 to avoid slowness
            try:
                # Use find command to search for the file
                cmd = f"find . -type f -name '*{name}*' 2>/dev/null | head -5"
                output = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    cwd=self.workspace,
                    timeout=10,
                )
                if output.stdout.strip():
                    results.append(f"Files matching '{name}':\n{output.stdout.strip()}")
            except Exception as e:
                print(f"  ⚠️ Pre-search error: {e}")

        if results:
            return "\n".join(results)
        return ""

    # ── Legacy brain mode (Ollama / Anthropic) ────────────────────

    def _run_with_legacy_brain(self, task: str) -> str:
        """
        Original agentic loop using AgentBrain (Ollama/Anthropic).

        Kept for backward compatibility when LLM_PROVIDER != cursor.
        """

        # ── Step 1: Build full context (repo + worktree) ──
        print("📸 Building context snapshot (repo knowledge + worktree state)...")
        context = self.context_builder.build_snapshot()

        # ── Step 2: Classify difficulty ──
        hard_problem = self.brain.classify_difficulty(task)
        if hard_problem:
            print("💡 Hard problem detected → enabling extended thinking\n")
        else:
            print("⚡ Standard task → normal mode\n")

        # ── Step 2.5: Pre-search for files mentioned in the task ──
        # This helps smaller models by giving them the file path directly
        pre_search_result = self._pre_search_files(task)

        # ── Step 3: Compose the full prompt ──
        # Keep it short — small models get overwhelmed by huge context.
        # The LLM can explore the workspace itself via tools.
        if pre_search_result:
            full_prompt = (
                f"TASK: {task}\n\n"
                f"RELEVANT FILE FOUND:\n{pre_search_result}\n\n"
                f"NEXT STEP: Call read_file on the file above, then use edit_file to make the changes."
            )
        else:
            full_prompt = (
                f"TASK: {task}\n\n"
                f"The workspace is at: {self.workspace}\n"
                f"Start by calling search_in_files to find the relevant file, then complete the task using tools."
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
            tool_names = {c.name for c in tool_calls}

            # Track file-edit tools by path (catches slight content variations)
            file_edit_tools = {"create_file", "edit_file"}
            read_tools = {"list_directory", "read_file"}
            for call in tool_calls:
                if call.name in file_edit_tools:
                    path = call.input.get("path", "unknown")
                    self._file_edit_count[path] = self._file_edit_count.get(path, 0) + 1

            # Check if any file has been edited/created 3+ times — model is stuck
            max_file_edits = max(self._file_edit_count.values()) if self._file_edit_count else 0
            if max_file_edits >= 3 and tool_names & file_edit_tools:
                stuck_path = max(self._file_edit_count, key=self._file_edit_count.get)
                print(f"  🛑 File '{stuck_path}' edited {max_file_edits}x — model stuck, force-breaking")
                break

            # Also detect exact-same-call repetition
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

                repeated_names = tool_names

                # After 1 nudge on file-creation tools, the file is done — force-break
                # After 2 nudges on any tool, the model is hopelessly stuck — force-break
                if (self._total_nudges >= 1 and repeated_names & file_edit_tools) or self._total_nudges >= 2:
                    print(f"  🛑 Model stuck in loop after {self._total_nudges} nudges — force-breaking (files already created)")
                    break

                # Context-aware nudge message
                if repeated_names & file_edit_tools:
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

        return final_summary

    # ── Deterministic Gates ─────────────────────────────────────────

    def _run_gates(self, task: str) -> bool:
        """
        Run the deterministic gate pipeline (.saturn/ config).

        Returns True if gates passed (or were skipped), False if blocked.
        When a retryable gate fails, delegates to Cursor CLI to fix,
        then re-runs the gate automatically.
        """
        import os
        force_gates = os.environ.get("FORCE_GATES", "").lower() in ("true", "1", "yes")

        if not self.files_changed and not force_gates:
            print("\n🚧 No files changed — skipping gates")
            return True

        if force_gates and not self.files_changed:
            print("\n🚧 FORCE_GATES=true — running gates without file changes (test mode)")

        print(f"\n{'─'*40}")
        if self.use_goose or self.use_hybrid:
            mode_label = "Goose-orchestrated" if self.use_goose else "Cursor+Goose hybrid"
            print(f"🚧 Running deterministic gates ({mode_label} mode)...")
            print("   Goose already ran Tier-1 compile + Tier-2 unit tests via MCP.")
            print("   Gate pipeline: risk check + Tier-1 static validation only.")
        else:
            print("🚧 Running deterministic gates...")
        print(f"{'─'*40}")

        resolve_affected = self._resolve_affected_modules_llm if (self.cursor or self.goose) else None
        pipeline = GatePipeline(
            workspace=self.workspace,
            fix_callback=self._gate_fix_callback,
            max_retries=5,
            timeout_per_gate=600,
            goose_orchestrated=(self.use_goose or self.use_hybrid),
            resolve_affected_modules=resolve_affected,
        )
        self.gates_result = pipeline.run()

        print(f"\n{self.gates_result.summary}")

        return self.gates_result.passed

    def _resolve_affected_modules_llm(self, workspace: str, changed_files: list[str]) -> set[str] | None:
        """
        When ZDPAS path-based auto-detect finds no affected modules, ask the LLM.
        Returns a set of module names to test, or None to run all tests.
        """
        known = set(ZDPAS_MODULE_MAPPING.values())
        context = "Changed files:\n" + "\n".join(f"  - {f}" for f in changed_files[:50])
        if len(changed_files) > 50:
            context += f"\n  ... and {len(changed_files) - 50} more"
        question = (
            "Which ZDPAS modules should we run tests for? "
            "Reply with ONLY a comma-separated list of module names. "
            "if changed files has ZDFilter or ZDFilterSuite, then run tests for the whole filter (com.zoho.dpaas.transformer.ZDFilterSuite) module"
            f"Known modules: {', '.join(sorted(known))}."
        )
        try:
            if self.cursor:
                reply = self.cursor.run_query(
                    question, context, workspace, timeout=60
                )
            elif self.goose:
                reply = self.goose._cli.run_query(
                    question, context, workspace, timeout=60
                )
            else:
                return None
        except Exception:
            return None
        modules = _parse_llm_module_list(reply, known)
        return modules if modules else None

    def _gate_fix_callback(self, gate_name: str, error_output: str, workspace: str) -> bool:
        """
        Called when a retryable gate fails.
        Asks the Cursor CLI, Goose CLI, or legacy brain to fix the problem.

        In hybrid mode (cursor+goose):
          1. Cursor applies the code fix (its LLM does the heavy lifting)
          2. Goose validates the fix via MCP (compile_quick + run_module_tests)
             before returning, so the full gate re-run has a high chance of passing

        Returns True if the agent applied a fix (all gates re-run from start).
        """
        lines = error_output.strip().splitlines()
        if len(lines) > 80:
            truncated = (
                "\n".join(lines[:10]) +
                "\n\n... (truncated) ...\n\n" +
                "\n".join(lines[-60:])
            )
        else:
            truncated = error_output

        fix_prompt = (
            f"A validation gate [{gate_name}] failed with the following output:\n\n"
            f"```\n{truncated}\n```\n\n"
            f"Please fix the code so this gate passes. "
            f"The entire gate pipeline (compile → test) will be re-run automatically "
            f"after your fix. Do NOT commit or push."
        )

        if self.use_hybrid and self.cursor and self.goose:
            # Hybrid: Cursor fixes the code, then Goose validates via MCP
            print(f"  🖥️🪿 Hybrid fix for [{gate_name}]: Cursor codes → Goose validates...")
            cursor_result = self.cursor.run(prompt=fix_prompt, workspace=workspace)

            if cursor_result.files_changed:
                self.files_changed.extend(cursor_result.files_changed)
                self.files_changed = list(dict.fromkeys(self.files_changed))

            if not cursor_result.success and not cursor_result.files_changed:
                print(f"  ❌ Cursor could not produce a fix for [{gate_name}]")
                return False

            # Goose validates the fix using MCP tools before the gate re-runs
            print(f"  🪿  Goose validating Cursor's fix via MCP (compile_quick + tests)...")
            goose_result = self.goose.fix(
                gate_name=gate_name,
                error_output=truncated,
                files_changed=self.files_changed,
            )

            if goose_result.files_changed:
                # Goose may have made additional corrections
                self.files_changed.extend(goose_result.files_changed)
                self.files_changed = list(dict.fromkeys(self.files_changed))

            # Return True as long as either Cursor or Goose produced changes
            return bool(cursor_result.files_changed or goose_result.files_changed or cursor_result.success)

        elif self.use_cursor and self.cursor:
            print(f"  🖥️  Asking Cursor CLI to fix [{gate_name}]...")
            result = self.cursor.run(prompt=fix_prompt, workspace=workspace)

            if result.files_changed:
                self.files_changed.extend(result.files_changed)
                self.files_changed = list(dict.fromkeys(self.files_changed))
                return True
            return result.success

        elif self.use_goose and self.goose:
            print(f"  🪿  Asking GooseAgent to fix [{gate_name}] (same session — context preserved)...")
            result = self.goose.fix(
                gate_name=gate_name,
                error_output=truncated,
                files_changed=self.files_changed,
            )

            if result.files_changed:
                self.files_changed.extend(result.files_changed)
                self.files_changed = list(dict.fromkeys(self.files_changed))
                return True
            return result.success

        elif self.brain:
            self.brain.inject_tool_results([{
                "type": "tool_result",
                "tool_use_id": f"gate_fix_{gate_name}",
                "content": (
                    f"⚠️ GATE [{gate_name}] FAILED:\n\n{truncated}\n\n"
                    "Fix the code so this gate passes."
                ),
            }])
            response = self.brain.think("", hard_problem=False)
            tool_calls = self.brain.extract_tool_calls(response)
            if tool_calls:
                for call in tool_calls:
                    result = self.executor.execute(call.name, call.input)
                    if call.name in ("edit_file", "create_file") and "OK" in result:
                        path = call.input.get("path", "")
                        if path and path not in self.files_changed:
                            self.files_changed.append(path)
                return True
            return False

        return False

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
        print(f"  {commit_result[:2000] if commit_result else '(empty)'}")

        if "NOTHING TO COMMIT" in commit_result:
            print("  ℹ️  Nothing to commit — skipping push & MR")
            return

        # ── Push ──
        print("  🚀 Pushing to origin...")
        push_result = self.executor.execute("git_push", {})
        print(f"  {push_result[:2000] if push_result else '(empty)'}")

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

            if mr_result and "OK" in mr_result:
                for line in mr_result.split("\n"):
                    if "→" in line and "http" in line:
                        self.pr_url = line.split("→")[-1].strip()
                action = "updated" if "updated" in mr_result else "created"
                print(f"  ✅ MR {action}: {self.pr_url}")
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
        """Generate a structured MR description with root cause, changes, and testing."""
        files_list = "\n".join(f"- `{f}`" for f in self.files_changed) if self.files_changed else "- (none)"

        # Use structured summary when available (Goose mode)
        ss = self._structured_summary
        if ss and ss.found:
            analysis_section = ss.for_mr()
        else:
            analysis_section = (
                f"### Summary\n\n{summary[:1000] if summary else '(no summary)'}\n"
            )

        gates_section = ""
        if self.gates_result and not self.gates_result.skipped:
            gates_section = (
                f"\n### Gates\n"
                f"{self.gates_result.gates.summary if self.gates_result.gates.gate_results else '(no gates configured)'}\n"
                f"- **Retries used:** {self.gates_result.gates.total_retries}\n"
            )

        engine = (
            "Cursor CLI" if self.use_cursor
            else "Cursor + Goose" if self.use_hybrid
            else "Goose CLI"
        ) if (self.use_cursor or self.use_hybrid or self.use_goose) else settings.llm_provider

        return (
            f"## 🪐 Saturn — Autonomous Agent MR\n\n"
            f"### Task\n\n{task}\n\n"
            f"{analysis_section}\n"
            f"### Files Changed\n\n{files_list}\n"
            f"{gates_section}\n"
            f"### Details\n\n"
            f"| Metric | Value |\n"
            f"|--------|-------|\n"
            f"| Engine | `{engine}` |\n"
            f"| Branch | `{self.branch_name}` |\n"
            f"| Tests | {'✅ Passed' if self.tests_passed else '⚠️ Not verified'} |\n"
            f"| Gates | {'✅ Passed' if (not self.gates_result or self.gates_result.passed) else '❌ Failed'} |\n\n"
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

