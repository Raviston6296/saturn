"""
Task worker — pulls tasks from the queue, creates worktrees, and runs the agent.
"""

from __future__ import annotations

import asyncio
import time
import traceback

from config import settings
from server.models import TaskRequest, TaskResult
from dispatcher.workspace import RepoManager
from agent.agent import AutonomousAgent
from integrations.cliq import (
    send_channel_message,
    send_cliq_message,
    reply_to_thread,
    format_progress_message,
    format_completion_message,
    format_failure_message,
)


class TaskWorker:
    """Background worker that processes tasks using git worktrees."""

    def __init__(self, queue: asyncio.Queue[TaskRequest], repo_manager: RepoManager):
        self.queue = queue
        self.repo = repo_manager

    async def run(self):
        """Main worker loop — runs forever, processes one task at a time."""
        print("🔄 Saturn worker: waiting for tasks...")
        while True:
            try:
                task = await self.queue.get()
                await self._process_task(task)
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ Worker error: {e}")
                traceback.print_exc()

    async def _process_task(self, task: TaskRequest):
        """
        Process a single task:
        1. Fetch latest from origin
        2. Create a worktree for this task
        3. Run the autonomous agent inside it (includes deterministic gates)
        4. Collect results (gates_passed, gates_summary, etc.)
        5. Report back to Cliq
        6. Clean up the worktree
        """
        start_time = time.time()
        worktree_path = None
        result = TaskResult(task_id=task.id)

        try:
            print(f"\n{'='*60}")
            print(f"🤖 Processing task: {task.id}")
            print(f"📋 {task.description[:100]}")
            print(f"🏷️  Type: {task.task_type.value} | Branch: {task.branch_name}")
            print(f"{'='*60}\n")

            loop = asyncio.get_event_loop()

            # 1. Fetch latest changes from origin
            print("📡 Fetching latest from origin...")
            await self._post_progress(task, "fetching")
            await loop.run_in_executor(None, self.repo.refresh)

            # 2. Create a worktree for this task
            await self._post_progress(task, "worktree")
            worktree_path = await loop.run_in_executor(
                None, self.repo.create_worktree, task.id, task.branch_name
            )
            await self._post_progress(task, "worktree_done", f"Branch `{task.branch_name}` created")

            # 3. Run the autonomous agent inside the worktree
            await self._post_progress(task, "agent_start")
            agent = AutonomousAgent(
                workspace=str(worktree_path),
                repo_name=settings.gitlab_project_id,
                branch_name=task.branch_name,
                repo_manager=self.repo,
            )

            summary = await loop.run_in_executor(
                None, agent.run, task.description
            )

            # 4. Post gates progress (gates were already run inside agent.run();
            #    this notifies Cliq of the outcome)
            gates_icon = "✅" if (agent.gates_result and agent.gates_result.passed) else "⚠️"
            await self._post_progress(task, "gates", f"{gates_icon} Gates complete")

            # 5. Collect results
            elapsed = time.time() - start_time
            result.status = "completed"
            result.summary = summary
            result.pr_url = agent.pr_url or ""
            result.branch_name = task.branch_name
            result.files_changed = agent.files_changed
            result.test_passed = agent.tests_passed
            result.loop_count = agent.loop_count
            result.duration_seconds = round(elapsed, 1)

            # Capture gates results
            if agent.gates_result:
                result.gates_passed = agent.gates_result.passed
                result.gates_summary = agent.gates_result.summary
            else:
                result.gates_passed = True  # gates skipped → treat as passed

        except Exception as e:
            elapsed = time.time() - start_time
            result.status = "failed"
            result.error = str(e)
            result.duration_seconds = round(elapsed, 1)
            print(f"❌ Task {task.id} failed: {e}")
            traceback.print_exc()

        finally:
            # 5. Report back to Cliq
            await self._report_to_cliq(task, result)

            # 6. Clean up worktree
            if worktree_path:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None, self.repo.remove_worktree, task.id
                    )
                except Exception:
                    pass

        print(f"\n✅ Task {task.id} finished in {result.duration_seconds}s "
              f"[{result.status}]\n")

    async def _post_progress(self, task: TaskRequest, stage: str, detail: str = ""):
        """Post a progress update to Cliq: as thread reply when task has thread_id, else to channel."""
        msg = format_progress_message(stage, detail)
        try:
            if task.thread_id:
                await reply_to_thread(thread_message_id=task.thread_id, text=msg)
            else:
                # No thread (e.g. direct API task) — still send to channel so user sees progress
                channel = task.channel_id or settings.cliq_channel_unique_name
                if channel:
                    await send_channel_message(f"🪐 Task `{task.id}`: {msg}", channel_name=channel)
        except Exception as e:
            print(f"  ⚠️ Failed to post progress to Cliq: {e}")

    async def _report_to_cliq(self, task: TaskRequest, result: TaskResult):
        """Send the final result back to the Cliq thread (or channel as fallback)."""
        if result.status == "completed":
            message = format_completion_message(
                task_id=task.id,
                summary=result.summary,
                pr_url=result.pr_url,
                files_changed=result.files_changed,
                test_passed=result.test_passed,
                gates_passed=result.gates_passed,
                gates_summary=result.gates_summary,
                duration=result.duration_seconds,
                loop_count=result.loop_count,
            )
        else:
            message = format_failure_message(
                task_id=task.id,
                error=result.error,
                duration=result.duration_seconds,
            )

        try:
            if task.thread_id:
                # Task was started in a thread (e.g. by LLM or user) — reply in same thread
                await reply_to_thread(thread_message_id=task.thread_id, text=message)
            else:
                # Fallback to regular channel message
                await send_cliq_message(
                    channel_id=task.channel_id,
                    text=message,
                )
        except Exception as e:
            print(f"⚠️ Failed to send Cliq message: {e}")

