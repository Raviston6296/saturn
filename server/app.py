"""
FastAPI application factory for Saturn.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings
from dispatcher.workspace import RepoManager
from dispatcher.worker import TaskWorker
from dispatcher.queue import task_queue


async def _handle_cliq_poll_message(msg: dict):
    """Handle a message received via Cliq polling."""
    from server.models import TaskRequest, TaskType, TaskPriority
    from server.routes.cliq_webhook import _classify_task_type, _classify_priority, _generate_branch_name
    from integrations.cliq import send_channel_message

    text = msg.get("text", "").strip()
    if not text:
        return

    print(f"📥 Cliq poll: received message from {msg.get('sender', 'unknown')}: {text[:80]}")

    # Create task from polled message
    task_type = _classify_task_type(text)
    priority = _classify_priority(text)
    branch_name = _generate_branch_name(task_type, text)

    task = TaskRequest(
        raw_message=text,
        description=text,
        repo_url=settings.repo_url,
        repo_name=settings.gitlab_project_id,
        branch_name=branch_name,
        task_type=task_type,
        priority=priority,
        channel_id=settings.cliq_channel_unique_name,
        sender=msg.get("sender", "cliq-user"),
        thread_id=msg.get("id", ""),  # Use message ID for threading
    )

    # Acknowledge the task
    await send_channel_message(f"🪐 Saturn received task `{task.id}` — processing...")

    # Queue the task
    await task_queue.put(task)
    print(f"  ✅ Task {task.id} queued from Cliq poll")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    On startup:
      1. Initialize the persistent bare clone (or fetch if exists)
      2. Start the background task worker
      3. Start Cliq poller (if polling mode enabled)

    On shutdown:
      4. Stop the worker and poller
    """
    loop = asyncio.get_event_loop()

    # 1. Initialize repo manager
    repo_manager = RepoManager()
    if settings.repo_url:
        await loop.run_in_executor(None, repo_manager.ensure_repo)
        print(f"🪐 Saturn watching: {settings.repo_url}")
    else:
        print("⚠️ No REPO_URL configured — Saturn will only accept tasks with repo URLs in messages")

    # 2. Start the worker
    worker = TaskWorker(task_queue, repo_manager)
    worker_task = asyncio.create_task(worker.run())
    print("🤖 Saturn agent worker started")

    # 3. Start Cliq poller (if enabled)
    poller = None
    poller_task = None
    if settings.cliq_polling_mode and settings.cliq_channel_unique_name:
        from integrations.cliq import CliqPoller
        poller = CliqPoller(
            on_message=_handle_cliq_poll_message,
            channel_name=settings.cliq_channel_unique_name,
            poll_interval=settings.cliq_poll_interval,
        )
        poller_task = asyncio.create_task(poller.start())
        print(f"🔄 Cliq poller started (channel: {settings.cliq_channel_unique_name}, interval: {settings.cliq_poll_interval}s)")
    elif settings.cliq_polling_mode:
        print("⚠️ Cliq polling enabled but CLIQ_CHANNEL_UNIQUE_NAME not set")

    yield

    # Stop poller
    if poller:
        poller.stop()
    if poller_task:
        poller_task.cancel()
        try:
            await poller_task
        except asyncio.CancelledError:
            pass
        print("🔄 Cliq poller stopped")

    # Stop worker
    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass
    print("🤖 Saturn agent worker stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Saturn — Autonomous Coding Agent",
        description="One instance per repo. Watches Zoho Cliq for tasks, solves them end-to-end via git worktrees.",
        version="0.2.0",
        lifespan=lifespan,
    )

    # Register routes
    from server.routes.health import router as health_router
    from server.routes.cliq_webhook import router as cliq_router
    from server.routes.tasks import router as tasks_router

    app.include_router(health_router)
    app.include_router(cliq_router)
    app.include_router(tasks_router)

    return app
