"""
Zoho Cliq integration — send messages and manage threads.

Uses Zoho Cliq API v2:
  - Channel message:  POST /api/v2/channelsbyname/{channel}/message
  - Thread reply:     POST /api/v2/chats/{chat_id}/message  (with thread_message_id)

Task lifecycle in Cliq:
  1. Task received  → post message to channel (becomes the thread parent)
  2. Progress update → reply to thread via thread_message_id
  3. Task complete   → reply to thread with final summary + MR link

API Reference: https://www.zoho.com/cliq/help/restapi/v2/
"""

from __future__ import annotations

import httpx

from config import settings


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CLIQ_API_BASE = "https://cliq.zoho.in/api/v2"


def _cliq_headers() -> dict[str, str]:
    """Standard headers for Zoho Cliq API calls."""
    return {
        "Authorization": f"Zoho-oauthtoken {settings.cliq_auth_token}",
        "Content-Type": "application/json",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Channel Messages
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def send_channel_message(text: str, channel_name: str = "") -> dict:
    """
    Post a message to a Cliq channel via the chat endpoint.

    Uses POST /api/v2/chats/{chat_id}/message with sync_message=true
    so we get a message_id back (needed for thread replies).

    Falls back to /api/v2/channelsbyname/{channel}/message if no chat_id.

    Returns dict with status and message_id.
    """
    if not settings.cliq_auth_token:
        print(f"📨 [CLIQ DISABLED] {text[:150]}")
        return {"status": "skipped", "reason": "cliq not configured"}

    # Prefer chat endpoint (returns message_id with sync_message)
    if settings.cliq_chat_id:
        url = f"{CLIQ_API_BASE}/chats/{settings.cliq_chat_id}/message"
        payload = {
            "text": text,
            "sync_message": True,
        }
    else:
        # Fallback to channel endpoint (returns 204, no message_id)
        channel = channel_name or settings.cliq_channel_unique_name
        if not channel:
            print(f"📨 [CLIQ NO CHANNEL] {text[:150]}")
            return {"status": "skipped", "reason": "no channel configured"}
        url = f"{CLIQ_API_BASE}/channelsbyname/{channel}/message"
        payload = {
            "text": text,
            "bot": {"name": "Saturn"},
        }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, json=payload, headers=_cliq_headers())
            response.raise_for_status()

            # Parse message_id from response (only available with sync_message)
            message_id = ""
            if response.status_code == 200:
                try:
                    data = response.json()
                    message_id = data.get("message_id", "")
                except Exception:
                    pass

            print(f"  📨 Channel message sent (message_id={message_id or 'n/a'})")
            return {
                "status": "sent",
                "method": "channel",
                "message_id": str(message_id) if message_id else "",
            }
        except httpx.HTTPError as e:
            print(f"⚠️ Cliq channel message error: {e}")
            return {"status": "error", "error": str(e)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Thread Replies
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def reply_to_thread(
    thread_message_id: str,
    text: str,
    chat_id: str = "",
) -> dict:
    """
    Post a reply to a thread in Cliq.

    POST /api/v2/chats/{chat_id}/message
    Body: {
      "text": "reply text",
      "sync_message": true,
      "thread_message_id": "parent_message_id"
    }

    Args:
        thread_message_id: The message ID of the parent (first message in thread)
        text: The reply text
        chat_id: The chat ID (CT_xxx). Falls back to settings.cliq_chat_id.
    """
    if not thread_message_id or not settings.cliq_auth_token:
        return await send_channel_message(text)

    cid = chat_id or settings.cliq_chat_id
    if not cid:
        print("⚠️ No cliq_chat_id configured — falling back to channel message")
        return await send_channel_message(text)

    url = f"{CLIQ_API_BASE}/chats/{cid}/message"
    payload = {
        "text": text,
        "sync_message": True,
        "thread_message_id": thread_message_id,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, json=payload, headers=_cliq_headers())
            response.raise_for_status()
            print(f"  🧵 Thread reply sent (thread={thread_message_id[:20]}...)")
            return {
                "status": "sent",
                "method": "thread_reply",
                "thread_message_id": thread_message_id,
            }
        except httpx.HTTPError as e:
            print(f"⚠️ Cliq thread reply error: {e}")
            # Fall back to regular channel message
            return await send_channel_message(text)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Legacy wrapper (backward compat)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def send_cliq_message(
    channel_id: str = "",
    text: str = "",
    webhook_url: str | None = None,
) -> dict:
    """
    Send a message to a Zoho Cliq channel (backward-compatible wrapper).
    """
    if not text:
        return {"status": "skipped", "reason": "empty message"}
    if webhook_url:
        return await _send_via_webhook(webhook_url, text)
    return await send_channel_message(text)


async def _send_via_webhook(webhook_url: str, text: str) -> dict:
    """Send message via Cliq incoming webhook."""
    payload = {"text": text}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(webhook_url, json=payload)
            response.raise_for_status()
            return {"status": "sent", "method": "webhook"}
        except httpx.HTTPError as e:
            print(f"⚠️ Cliq webhook error: {e}")
            return {"status": "error", "error": str(e)}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Message Formatting
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def format_ack_message(task_id: str, description: str, task_type: str, priority: str) -> str:
    """Format the initial acknowledgment message (becomes the thread parent)."""
    return (
        f"🪐 *Saturn — Task `{task_id}`*\n\n"
        f"📋 *{description[:300]}*\n"
        f"🏷️ Type: `{task_type}` | Priority: `{priority}`\n\n"
        f"⏳ Working on it..."
    )


def format_progress_message(stage: str, detail: str = "") -> str:
    """Format a progress update for the thread."""
    stages = {
        "fetching": "📡 Fetching latest from origin...",
        "worktree": "🌿 Creating isolated worktree...",
        "agent_start": "🧠 Agent started — reasoning about the task...",
        "editing": "✏️ Making code changes...",
        "testing": "🧪 Running tests...",
        "committing": "💾 Committing changes...",
        "pushing": "🚀 Pushing to remote...",
        "creating_mr": "📝 Creating Merge Request...",
    }
    msg = stages.get(stage, f"🔄 {stage}")
    if detail:
        msg += f"\n  {detail}"
    return msg


def format_completion_message(
    task_id: str,
    summary: str,
    pr_url: str = "",
    files_changed: list[str] | None = None,
    test_passed: bool = False,
    duration: float = 0.0,
    loop_count: int = 0,
) -> str:
    """Format the final completion message for the thread."""
    status_emoji = "✅" if pr_url else "⚠️"

    sections = [
        f"{status_emoji} *Task Complete — `{task_id}`*\n",
    ]

    if summary:
        sections.append(f"📝 *Summary:*\n{summary[:500]}\n")

    if pr_url:
        sections.append(f"🔗 *Merge Request:* {pr_url}")

    if files_changed:
        file_list = "\n".join(f"  • `{f}`" for f in files_changed[:8])
        if len(files_changed) > 8:
            file_list += f"\n  ... and {len(files_changed) - 8} more"
        sections.append(f"📁 *Files Changed:*\n{file_list}")

    sections.append(
        f"\n{'✅' if test_passed else '❌'} Tests: {'Passed' if test_passed else 'Not verified'} "
        f"| ⏱️ {duration:.0f}s | 🔁 {loop_count} iterations"
    )

    return "\n".join(sections)


def format_failure_message(
    task_id: str,
    error: str,
    duration: float = 0.0,
) -> str:
    """Format a failure message for the thread."""
    return (
        f"❌ *Task Failed — `{task_id}`*\n\n"
        f"⚠️ *Error:*\n`{error[:400]}`\n\n"
        f"⏱️ Duration: {duration:.0f}s"
    )


def format_cliq_card(
    title: str,
    summary: str,
    pr_url: str = "",
    files_changed: list[str] | None = None,
    test_passed: bool = False,
    duration: float = 0.0,
) -> dict:
    """Format a rich Cliq message card (legacy)."""
    status_emoji = "✅" if test_passed else "⚠️"
    sections = [
        f"*{title}*\n",
        f"📝 {summary[:400]}",
    ]
    if pr_url:
        sections.append(f"\n🔗 *Merge Request:* {pr_url}")
    if files_changed:
        file_list = "\n".join(f"  • `{f}`" for f in files_changed[:8])
        if len(files_changed) > 8:
            file_list += f"\n  ... and {len(files_changed) - 8} more"
        sections.append(f"\n📁 *Files Changed:*\n{file_list}")
    sections.append(
        f"\n{status_emoji} Tests: {'Passed' if test_passed else 'Not verified'} "
        f"| ⏱️ {duration:.0f}s"
    )

    return {"text": "\n".join(sections)}
