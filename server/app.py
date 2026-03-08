"""
FastAPI application factory for Saturn.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from config import settings
from dispatcher.worker import TaskWorker
from dispatcher.queue import task_queue


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the background task worker on startup, clean up on shutdown."""
    worker = TaskWorker(task_queue)
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
        description="Monitors Zoho Cliq for tasks, solves them end-to-end, opens PRs.",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Register routes
    from server.routes.health import router as health_router
    from server.routes.cliq_webhook import router as cliq_router

    app.include_router(health_router)
    app.include_router(cliq_router)

    return app

