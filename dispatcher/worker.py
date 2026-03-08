"""
Task worker — pulls tasks from the queue and runs the autonomous agent.
"""

from __future__ import annotations

import asyncio
import time
import traceback

from config import settings
from server.models import TaskRequest, TaskResult
from dispatcher.workspace import Workspace
from agent.agent import AutonomousAgent
from integrations.cliq import send_cliq_message


class TaskWorker:
    """Background worker that processes tasks from the queue."""

    def __init__(self, queue: asyncio.Queue[TaskRequest]):
        self.queue = queue

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
        """Process a single task end-to-end."""
        start_time = time.time()
        workspace = None
        result = TaskResult(task_id=task.id)

        try:
            print(f"\n{'='*60}")
            print(f"🤖 Processing task: {task.id}")
            print(f"📋 {task.description[:100]}")
            print(f"🏷️  Type: {task.task_type.value} | Repo: {task.repo_name}")
            print(f"{'='*60}\n")

            # 1. Set up isolated workspace
            workspace = Workspace(
                task_id=task.id,
                repo_url=task.repo_url,
                branch_name=task.branch_name,
            )

            # Run workspace setup in thread pool (blocking IO)
            loop = asyncio.get_event_loop()
            workspace_path = await loop.run_in_executor(None, workspace.setup)

            # 2. Run the autonomous agent (blocking — runs in thread pool)
            agent = AutonomousAgent(
                workspace=str(workspace_path),
                repo_name=task.repo_name,
                branch_name=task.branch_name,
            )

            summary = await loop.run_in_executor(
                None, agent.run, task.description
            )

            # 3. Collect results
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
            # 4. Report back to Cliq
            await self._report_to_cliq(task, result)

            # 5. Clean up workspace
            if workspace:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, workspace.cleanup)
                except Exception:
                    pass

        print(f"\n✅ Task {task.id} finished in {result.duration_seconds}s "
              f"[{result.status}]\n")

    async def _report_to_cliq(self, task: TaskRequest, result: TaskResult):
        """Send the final result back to the Cliq channel."""
        if result.status == "completed":
            emoji = "✅"
            status_text = "Completed"
        else:
            emoji = "❌"
            status_text = "Failed"

        message = (
            f"{emoji} **Saturn Task {task.id} — {status_text}**\n\n"
        )

        if result.summary:
            message += f"📝 **Summary:**\n{result.summary[:500]}\n\n"

        if result.pr_url:
            message += f"🔗 **PR:** {result.pr_url}\n"

        if result.files_changed:
            files_list = "\n".join(f"  • `{f}`" for f in result.files_changed[:10])
            message += f"📁 **Files changed:**\n{files_list}\n"

        if result.error:
            message += f"⚠️ **Error:** {result.error[:200]}\n"

        message += (
            f"\n⏱️ Duration: {result.duration_seconds}s | "
            f"🔁 Loops: {result.loop_count} | "
            f"🧪 Tests: {'✅' if result.test_passed else '❌'}"
        )

        try:
            await send_cliq_message(
                channel_id=task.channel_id,
                text=message,
            )
        except Exception as e:
            print(f"⚠️ Failed to send Cliq message: {e}")

