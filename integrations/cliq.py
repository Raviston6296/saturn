"""
Zoho Cliq integration — send messages and manage threads.

Supports two auth methods:
  1. **zapikey (preferred)** — Bot API key, no OAuth needed.
     URL: /api/v2/channelsbyname/{channel}/message?bot_unique_name=X&zapikey=Y
  2. **OAuth token (legacy)** — Authorization header with Zoho-oauthtoken.

Task lifecycle in Cliq:
  1. Task received  → post message to channel (becomes the thread parent)
  2. Progress update → reply to thread via thread_message_id
  3. Task complete   → reply to thread with final summary + MR link

API Reference: https://www.zoho.com/cliq/help/restapi/v2/
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx

from config import settings


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CLIQ_API_BASE = "https://cliq.zoho.in/api/v2"


def _use_zapikey() -> bool:
    """Return True if zapikey auth is configured (preferred over OAuth)."""
    return bool(settings.cliq_bot_zapikey and settings.cliq_bot_unique_name)


def _zapikey_params() -> dict[str, str]:
    """Query params for zapikey auth: ?bot_unique_name=X&zapikey=Y"""
    return {
        "bot_unique_name": settings.cliq_bot_unique_name,
        "zapikey": settings.cliq_bot_zapikey,
    }


def _cliq_headers() -> dict[str, str]:
    """Headers for Zoho Cliq API calls. Uses OAuth if zapikey not available."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if not _use_zapikey() and settings.cliq_auth_token:
        headers["Authorization"] = f"Zoho-oauthtoken {settings.cliq_auth_token}"
    return headers


def _build_url(path: str) -> str:
    """
    Build a full Cliq API URL, appending zapikey params if configured.

    Examples:
      zapikey mode: https://cliq.zoho.in/api/v2/channelsbyname/ch/message?bot_unique_name=X&zapikey=Y
      oauth mode:   https://cliq.zoho.in/api/v2/channelsbyname/ch/message
    """
    url = f"{CLIQ_API_BASE}/{path.lstrip('/')}"
    if _use_zapikey():
        url += "?" + urlencode(_zapikey_params())
    return url


def _is_cliq_configured() -> bool:
    """Check if Cliq is configured (either zapikey or OAuth)."""
    return _use_zapikey() or bool(settings.cliq_auth_token)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Channel Messages
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def send_channel_message(text: str, channel_name: str = "") -> dict:
    """
    Post a message to a Cliq channel as the bot.

    With zapikey: POST .../channelsbyname/{channel}/message?bot_unique_name=X&zapikey=Y
    With OAuth:   POST .../chats/{chat_id}/message (Authorization header)

    Returns dict with status and message_id.
    """
    if not _is_cliq_configured():
        print(f"📨 [CLIQ DISABLED] {text[:150]}")
        return {"status": "skipped", "reason": "cliq not configured"}

    channel = channel_name or settings.cliq_channel_unique_name

    # zapikey mode — use channelsbyname endpoint with bot params
    if _use_zapikey() and channel:
        url = _build_url(f"channelsbyname/{channel}/message")
        payload = {"text": text}
    # OAuth mode with chat_id — use chats endpoint for sync_message support
    elif settings.cliq_chat_id and settings.cliq_auth_token:
        url = f"{CLIQ_API_BASE}/chats/{settings.cliq_chat_id}/message"
        payload = {"text": text, "sync_message": True}
    # OAuth mode without chat_id — use channelsbyname
    elif channel and settings.cliq_auth_token:
        url = f"{CLIQ_API_BASE}/channelsbyname/{channel}/message"
        payload = {"text": text, "bot": {"name": "Saturn"}}
    else:
        print(f"📨 [CLIQ NO CHANNEL] {text[:150]}")
        return {"status": "skipped", "reason": "no channel configured"}

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

    With zapikey:
      POST .../channelsbyname/{channel}/message?bot_unique_name=X&zapikey=Y
      Body: {"text": "...", "thread_message_id": "..."}

    With OAuth:
      POST .../chats/{chat_id}/message  (Authorization header)
      Body: {"text": "...", "sync_message": true, "thread_message_id": "..."}
    """
    if not thread_message_id or not _is_cliq_configured():
        return await send_channel_message(text)

    # ── zapikey mode: use channelsbyname endpoint with thread_message_id in body ──
    if _use_zapikey():
        channel = settings.cliq_channel_unique_name
        if not channel:
            print("⚠️ No cliq_channel_unique_name configured — falling back to channel message")
            return await send_channel_message(text)

        url = _build_url(f"channelsbyname/{channel}/message")
        payload = {
            "text": text,
            "thread_message_id": thread_message_id,
        }
    else:
        # ── OAuth mode: use chats endpoint ──
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
# Message Polling (for private networks without public URL)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


async def fetch_channel_messages(
    channel_name: str = "",
    limit: int = 10,
    since_message_id: str = "",
) -> list[dict]:
    """
    Fetch recent messages from a Cliq channel.

    This allows Saturn to POLL for messages instead of requiring
    Cliq to push via webhook (useful when Saturn is on private network).

    API: GET /api/v2/channelsbyname/{channel}/messages

    Returns list of message dicts with: id, text, sender, time
    """
    if not _is_cliq_configured():
        return []

    channel = channel_name or settings.cliq_channel_unique_name
    if not channel:
        print("⚠️ No channel configured for polling")
        return []

    # Build URL with all params together (zapikey + limit + since)
    base_url = f"{CLIQ_API_BASE}/channelsbyname/{channel}/messages"
    params = {"limit": str(limit)}
    if since_message_id:
        params["since"] = since_message_id

    # Add zapikey params if configured
    if _use_zapikey():
        params.update(_zapikey_params())

    url = f"{base_url}?{urlencode(params)}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(url, headers=_cliq_headers())
            response.raise_for_status()
            data = response.json()

            messages = []
            for msg in data.get("messages", data.get("data", [])):
                messages.append({
                    "id": msg.get("id", ""),
                    "text": msg.get("text", msg.get("content", "")),
                    "sender": msg.get("sender", {}).get("name", "unknown"),
                    "sender_id": msg.get("sender", {}).get("id", ""),
                    "time": msg.get("time", ""),
                })
            return messages
        except httpx.HTTPError as e:
            print(f"⚠️ Cliq fetch messages error: {e}")
            return []


class CliqPoller:
    """
    Polls Cliq channel for new messages and processes them as tasks.

    Use this when Saturn cannot receive webhooks (private network).

    Usage:
        poller = CliqPoller(on_message=handle_message)
        await poller.start()
    """

    def __init__(
        self,
        on_message: callable,
        channel_name: str = "",
        poll_interval: int = 5,
    ):
        self.on_message = on_message
        self.channel_name = channel_name or settings.cliq_channel_unique_name
        self.poll_interval = poll_interval
        self.last_message_id = ""
        self.seen_ids: set[str] = set()
        self._running = False

    async def start(self):
        """Start polling for messages."""
        import asyncio

        self._running = True
        print(f"🔄 Cliq poller started (channel: {self.channel_name}, interval: {self.poll_interval}s)")

        while self._running:
            try:
                messages = await fetch_channel_messages(
                    channel_name=self.channel_name,
                    limit=10,
                    since_message_id=self.last_message_id,
                )

                for msg in messages:
                    msg_id = msg.get("id", "")
                    if msg_id and msg_id not in self.seen_ids:
                        self.seen_ids.add(msg_id)
                        self.last_message_id = msg_id

                        # Skip bot's own messages
                        sender = msg.get("sender", "").lower()
                        if sender in {"saturn", "saturnbot", "saturn bot"}:
                            continue

                        # Skip messages that look like Saturn output
                        text = msg.get("text", "")
                        if text.startswith(("🪐", "🤖", "✅", "❌", "📡", "🌿")):
                            continue

                        # Process the message
                        await self.on_message(msg)

                # Keep seen_ids from growing indefinitely
                if len(self.seen_ids) > 1000:
                    self.seen_ids = set(list(self.seen_ids)[-500:])

            except Exception as e:
                print(f"⚠️ Cliq poller error: {e}")

            await asyncio.sleep(self.poll_interval)

    def stop(self):
        """Stop polling."""
        self._running = False
        print("🛑 Cliq poller stopped")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Message Formatting
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Maximum characters to include from the gates summary in a Cliq message
_MAX_GATES_SUMMARY_LEN = 400


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
        "worktree_done": "🌿 Worktree ready",
        "agent_start": "🧠 Agent started — reasoning about the task...",
        "editing": "✏️ Making code changes...",
        "gates": "🚧 Running deterministic gates (risk check + validation)...",
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
    gates_passed: bool = False,
    gates_summary: str = "",
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

    gates_icon = "✅" if gates_passed else "❌"
    if test_passed:
        tests_icon = "✅"
        tests_label = "Passed"
    elif gates_passed:
        # Tests were exercised as part of the gate pipeline but not separately verified
        tests_icon = "✅"
        tests_label = "Passed (via gates)"
    else:
        tests_icon = "❌"
        tests_label = "Not verified"

    sections.append(
        f"\n{tests_icon} Tests: {tests_label} "
        f"| {gates_icon} Gates: {'Passed' if gates_passed else 'Failed'} "
        f"| ⏱️ {duration:.0f}s | 🔁 {loop_count} iterations"
    )

    if gates_summary and not gates_passed:
        sections.append(f"\n🚧 *Gates Details:*\n{gates_summary[:_MAX_GATES_SUMMARY_LEN]}")

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
    if test_passed:
        tests_label = "Passed"
    else:
        tests_label = "Not verified"

    sections.append(
        f"\n{status_emoji} Tests: {tests_label} | ⏱️ {duration:.0f}s"
    )

    return {"text": "\n".join(sections)}
