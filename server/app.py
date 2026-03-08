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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    On startup:
      1. Initialize the persistent bare clone (or fetch if exists)
      2. Start the background task worker

    On shutdown:
      3. Stop the worker
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

    yield

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

