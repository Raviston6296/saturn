"""
Pydantic models for Zoho Cliq webhook payloads and internal task objects.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskType(str, Enum):
    BUG_FIX = "bug_fix"
    FEATURE = "feature"
    REFACTOR = "refactor"
    TEST = "test"
    DOCS = "docs"
    UNKNOWN = "unknown"


class CliqMessage(BaseModel):
    """Incoming message payload from Zoho Cliq webhook."""
    name: Optional[str] = ""
    message: Optional[str] = ""
    chat_id: Optional[str] = ""
    channel_name: Optional[str] = ""
    sender_id: Optional[str] = ""
    message_id: Optional[str] = ""            # Original message ID for thread replies
    timestamp: Optional[str] = None


def _generate_id() -> str:
    return f"SATURN-{uuid.uuid4().hex[:8].upper()}"


class TaskRequest(BaseModel):
    """Internal representation of a task extracted from a Cliq message."""
    id: str = Field(default_factory=_generate_id)
    raw_message: str
    description: str
    repo_url: str = ""
    repo_name: str = ""
    branch_name: str = ""
    task_type: TaskType = TaskType.UNKNOWN
    priority: TaskPriority = TaskPriority.MEDIUM
    channel_id: str = ""
    sender: str = ""
    thread_id: str = ""                       # Cliq thread_message_id for thread replies
    created_at: datetime = Field(default_factory=datetime.now)
    status: str = "pending"


class TaskResult(BaseModel):
    """Result after the agent finishes processing a task."""
    task_id: str
    status: str = "completed"
    summary: str = ""
    structured_summary: str = ""  # Pre-formatted Cliq summary (root cause + changes)
    pr_url: str = ""
    branch_name: str = ""
    files_changed: list[str] = []
    test_passed: bool = False
    gates_passed: bool = False
    gates_summary: str = ""
    loop_count: int = 0
    duration_seconds: float = 0.0
    error: str = ""

