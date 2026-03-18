"""
Zoho Cliq webhook endpoint.

Receives messages from a Cliq channel, parses them into tasks,
enqueues them for the autonomous agent, and acks immediately.

Flow:
  1. User posts a message in Cliq → Deluge script forwards it with message_id
  2. Saturn replies TO that message (thread) with an ack
  3. All progress updates and final result go as thread replies to the user's message
"""

from __future__ import annotations

import re
import hashlib
from typing import Any

from fastapi import APIRouter, BackgroundTasks

from config import settings
from server.models import CliqMessage, TaskRequest, TaskType, TaskPriority
from dispatcher.queue import task_queue
from integrations.cliq import (
    reply_to_thread,
    format_ack_message,
)

router = APIRouter(prefix="/webhook", tags=["cliq"])

# ── Bot-message detection ────────────────────────────────────────
# These patterns help identify Saturn's own messages to prevent
# feedback loops where the bot triggers itself.
_SATURN_NAMES = {"saturn", "saturn bot", "saturn-bot"}
_SATURN_PREFIXES = ("🪐", "🤖", "✅ *Task Complete", "❌ *Task Failed", "📡 Fetching", "🌿 Creating", "🧠 Agent started", "✏️ Making", "🧪 Running", "💾 Committing", "🚀 Pushing", "📝 Creating Merge", "🔄 ")


def _is_saturn_message(message: CliqMessage) -> bool:
    """Check if a message was sent by Saturn itself (prevents feedback loop)."""
    # Check sender name
    name = (message.name or "").strip().lower()
    if name in _SATURN_NAMES:
        return True

    # Check if message text starts with known Saturn output patterns
    text = (message.message or "").strip()
    if any(text.startswith(p) for p in _SATURN_PREFIXES):
        return True

    # Check for Saturn task ID pattern in the message (e.g. "SATURN-ABCD1234")
    if re.match(r".*\bSATURN-[A-F0-9]{8}\b", text):
        return True

    return False


@router.post("/cliq")
async def cliq_webhook(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
):
    """
    Receives an incoming message from Zoho Cliq.
    Uses the user's original message_id as the thread parent so all
    replies appear under the user's message.
    """
    message = _parse_cliq_payload(payload)

    if not (message.message or "").strip():
        return {"text": "Empty message — nothing to do."}

    # ── Prevent feedback loops ──
    # Ignore messages that came from Saturn itself
    if _is_saturn_message(message):
        print(f"  🔇 Ignoring own message from '{message.name}': {(message.message or '')[:80]}")
        return {"status": "ignored", "reason": "own message"}

    task = _extract_task(message)

    # ── Use the user's original message_id as thread parent ──
    # The Deluge script sends message_id of the user's post.
    # We thread all replies (ack, progress, result) under it.
    thread_message_id = message.message_id or ""

    if thread_message_id:
        task.thread_id = thread_message_id
        print(f"  🧵 Will reply to user's message: {thread_message_id}")

        # Post ack as a thread reply to the user's message
        ack_text = format_ack_message(
            task_id=task.id,
            description=task.description,
            task_type=task.task_type.value,
            priority=task.priority.value,
        )
        await reply_to_thread(thread_message_id=thread_message_id, text=ack_text)
    else:
        print("  ⚠️ No message_id from Cliq — cannot thread replies")

    await task_queue.put(task)

    return {"text": f"🤖 Saturn received task `{task.id}` — queued ✅"}


def _parse_cliq_payload(payload: dict[str, Any]) -> CliqMessage:
    """Parse various Cliq webhook payload formats into our model."""
    if "name" in payload and "message" in payload:
        return CliqMessage(
            name=payload.get("name") or "",
            message=payload.get("message") or "",
            chat_id=payload.get("chat_id") or "",
            channel_name=payload.get("channel_name") or "",
            sender_id=payload.get("sender_id") or "",
            message_id=payload.get("message_id") or "",
        )
    if "text" in payload:
        return CliqMessage(
            message=payload.get("text") or "",
            chat_id=(payload.get("chat") or {}).get("id") or "",
            name=(payload.get("sender") or {}).get("name") or "unknown",
            message_id=payload.get("message_id") or "",
        )
    msg = payload.get("message") or payload.get("text") or payload.get("content") or ""
    return CliqMessage(
        message=str(msg),
        chat_id=payload.get("chat_id") or "",
        message_id=payload.get("message_id") or "",
    )


def _extract_task(message: CliqMessage) -> TaskRequest:
    """Extract a structured task from a Cliq message."""
    text = (message.message or "").strip()
    task_type = _classify_task_type(text)
    priority = _classify_priority(text)
    repo_name = _extract_repo(text) or settings.gitlab_project_id
    branch_name = _generate_branch_name(task_type, text)

    return TaskRequest(
        raw_message=text,
        description=text,
        repo_url=settings.repo_url if repo_name else "",
        repo_name=repo_name,
        branch_name=branch_name,
        task_type=task_type,
        priority=priority,
        channel_id=message.chat_id or "",
        sender=message.name or "",
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
    """Extract project path from message text (supports GitLab and GitHub URLs)."""
    # Match GitLab/GitHub URLs
    url_match = re.search(r"(?:gitlab|github)\.[\w.-]+[/:]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)", text)
    if url_match:
        return url_match.group(1).removesuffix(".git")
    # Match group/project pattern
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

