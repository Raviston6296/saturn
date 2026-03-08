"""
Zoho Cliq webhook endpoint.

Receives messages from a Cliq channel, parses them into tasks,
enqueues them for the autonomous agent, and acks immediately.
"""

from __future__ import annotations

import re
import hashlib
from typing import Any

from fastapi import APIRouter, BackgroundTasks

from config import settings
from server.models import CliqMessage, TaskRequest, TaskType, TaskPriority
from dispatcher.queue import task_queue
from integrations.cliq import send_cliq_message

router = APIRouter(prefix="/webhook", tags=["cliq"])


@router.post("/cliq")
async def cliq_webhook(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
):
    """
    Receives an incoming message from Zoho Cliq.
    Parses the task, enqueues it, and acks with a tracking message.
    """
    message = _parse_cliq_payload(payload)

    if not message.message.strip():
        return {"text": "Empty message — nothing to do."}

    if message.name.lower() == "saturn":
        return {"status": "ignored", "reason": "own message"}

    task = _extract_task(message)

    await task_queue.put(task)

    ack_text = (
        f"🤖 **Saturn received task** `{task.id}`\n"
        f"📋 *{task.description[:120]}*\n"
        f"🏷️ Type: `{task.task_type.value}` | Priority: `{task.priority.value}`\n"
        f"⏳ Working on it..."
    )

    background_tasks.add_task(
        send_cliq_message,
        channel_id=task.channel_id,
        text=ack_text,
    )

    return {"text": ack_text}


def _parse_cliq_payload(payload: dict[str, Any]) -> CliqMessage:
    """Parse various Cliq webhook payload formats into our model."""
    if "name" in payload and "message" in payload:
        return CliqMessage(
            name=payload.get("name", ""),
            message=payload.get("message", ""),
            chat_id=payload.get("chat_id", ""),
            channel_name=payload.get("channel_name", ""),
            sender_id=payload.get("sender_id", ""),
        )
    if "text" in payload:
        return CliqMessage(
            message=payload["text"],
            chat_id=payload.get("chat", {}).get("id", ""),
            name=payload.get("sender", {}).get("name", "unknown"),
        )
    msg = payload.get("message", payload.get("text", payload.get("content", "")))
    return CliqMessage(message=str(msg), chat_id=payload.get("chat_id", ""))


def _extract_task(message: CliqMessage) -> TaskRequest:
    """Extract a structured task from a Cliq message."""
    text = message.message.strip()
    task_type = _classify_task_type(text)
    priority = _classify_priority(text)
    repo_name = _extract_repo(text) or settings.github_default_repo
    branch_name = _generate_branch_name(task_type, text)

    return TaskRequest(
        raw_message=text,
        description=text,
        repo_url=f"https://github.com/{repo_name}.git" if repo_name else "",
        repo_name=repo_name,
        branch_name=branch_name,
        task_type=task_type,
        priority=priority,
        channel_id=message.chat_id,
        sender=message.name,
    )


def _classify_task_type(text: str) -> TaskType:
    t = text.lower()
    if any(w in t for w in ["bug", "fix", "broken", "error", "crash", "fail"]):
        return TaskType.BUG_FIX
    if any(w in t for w in ["feature", "add", "implement", "create", "build"]):
        return TaskType.FEATURE
    if any(w in t for w in ["refactor", "clean", "reorganize", "migrate"]):
        return TaskType.REFACTOR
    if any(w in t for w in ["test", "coverage", "spec"]):
        return TaskType.TEST
    if any(w in t for w in ["doc", "readme", "comment"]):
        return TaskType.DOCS
    return TaskType.UNKNOWN


def _classify_priority(text: str) -> TaskPriority:
    t = text.lower()
    if any(w in t for w in ["urgent", "critical", "asap", "production down", "p0"]):
        return TaskPriority.CRITICAL
    if any(w in t for w in ["important", "high", "p1"]):
        return TaskPriority.HIGH
    if any(w in t for w in ["low", "minor", "nice to have", "p3"]):
        return TaskPriority.LOW
    return TaskPriority.MEDIUM


def _extract_repo(text: str) -> str | None:
    """Extract owner/repo from message text."""
    gh_match = re.search(r"github\.com[/:]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", text)
    if gh_match:
        return gh_match.group(1).removesuffix(".git")
    repo_match = re.search(r"\b([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)\b", text)
    if repo_match:
        candidate = repo_match.group(1)
        if len(candidate) > 5 and "/" in candidate:
            return candidate
    return None


def _generate_branch_name(task_type: TaskType, text: str) -> str:
    """Generate a git branch name from the task."""
    prefix_map = {
        TaskType.BUG_FIX: "fix",
        TaskType.FEATURE: "feat",
        TaskType.REFACTOR: "refactor",
        TaskType.TEST: "test",
        TaskType.DOCS: "docs",
        TaskType.UNKNOWN: "task",
    }
    prefix = prefix_map[task_type]
    words = re.sub(r"[^a-z0-9\s]", "", text.lower()).split()[:5]
    slug = "-".join(words) if words else "auto"
    short_hash = hashlib.md5(text.encode()).hexdigest()[:6]
    branch = f"saturn/{prefix}/{slug}-{short_hash}"
    return branch[:60]

