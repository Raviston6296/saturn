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
        3. Run the autonomous agent inside it
        4. Clean up the worktree
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

            # 4. Collect results
            elapsed = time.time() - start_time
            result.status = "completed"
            result.summary = summary
            result.pr_url = agent.pr_url or ""
            result.branch_name = task.branch_name
            result.files_changed = agent.files_changed
            result.test_passed = agent.tests_passed
            result.loop_count = agent.loop_count
            result.duration_seconds = round(elapsed, 1)

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
        """Post a progress update to the task's Cliq thread (if available)."""
        if not task.thread_id:
            return
        try:
            msg = format_progress_message(stage, detail)
            await reply_to_thread(thread_message_id=task.thread_id, text=msg)
        except Exception as e:
            print(f"  ⚠️ Failed to post progress to thread: {e}")

    async def _report_to_cliq(self, task: TaskRequest, result: TaskResult):
        """Send the final result back to the Cliq thread (or channel as fallback)."""
        if result.status == "completed":
            message = format_completion_message(
                task_id=task.id,
                summary=result.summary,
                pr_url=result.pr_url,
                files_changed=result.files_changed,
                test_passed=result.test_passed,
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
                # Post as a thread reply — keeps everything grouped
                await reply_to_thread(thread_message_id=task.thread_id, text=message)
            else:
                # Fallback to regular channel message
                await send_cliq_message(
                    channel_id=task.channel_id,
                    text=message,
                )
        except Exception as e:
            print(f"⚠️ Failed to send Cliq message: {e}")

