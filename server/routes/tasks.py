"""
Direct task submission endpoint — for testing without Zoho Cliq.

POST /tasks/submit  →  submit a plain-text task directly
GET  /tasks/status   →  check queue size / worker status
"""

from __future__ import annotations


from fastapi import APIRouter
from pydantic import BaseModel

from server.models import TaskRequest, TaskType, TaskPriority
from server.routes.cliq_webhook import _generate_branch_name
from dispatcher.queue import task_queue

router = APIRouter(prefix="/tasks", tags=["tasks"])


class DirectTaskPayload(BaseModel):
    """Simple task submission payload for testing."""
    description: str
    task_type: str = "unknown"
    priority: str = "medium"
    branch_name: str = ""


@router.post("/submit")
async def submit_task(payload: DirectTaskPayload):
    """
    Submit a task directly (no Cliq involved).
    Great for local testing and development.
    """
    # Match enum values
    task_type = TaskType.UNKNOWN
    for tt in TaskType:
        if tt.value == payload.task_type:
            task_type = tt
            break

    priority = TaskPriority.MEDIUM
    for tp in TaskPriority:
        if tp.value == payload.priority:
            priority = tp
            break

    # Generate a branch name if not provided
    branch_name = payload.branch_name or _generate_branch_name(task_type, payload.description)

    task = TaskRequest(
        raw_message=payload.description,
        description=payload.description,
        task_type=task_type,
        priority=priority,
        branch_name=branch_name,
        sender="test-user",
        channel_id="test-channel",
    )

    await task_queue.put(task)

    return {
        "status": "queued",
        "task_id": task.id,
        "description": task.description[:120],
        "task_type": task.task_type.value,
        "priority": task.priority.value,
        "queue_size": task_queue.qsize(),
    }


@router.get("/status")
async def task_status():
    """Check the current queue status."""
    return {
        "queue_size": task_queue.qsize(),
        "queue_maxsize": task_queue.maxsize,
        "queue_empty": task_queue.empty(),
    }

