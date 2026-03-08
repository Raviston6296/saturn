"""
Zoho Cliq integration — send messages back to Cliq channels.

Supports two modes:
  1. Bot API: POST to Cliq channel via Zoho OAuth token
  2. Incoming Webhook: POST to a webhook URL (simpler setup)
"""

from __future__ import annotations

import httpx

from config import settings


async def send_cliq_message(
    channel_id: str = "",
    text: str = "",
    webhook_url: str | None = None,
) -> dict:
    """
    Send a message to a Zoho Cliq channel.

    Uses the bot API if configured, otherwise falls back to webhook URL.
    """
    if not text:
        return {"status": "skipped", "reason": "empty message"}

    # Method 1: Direct webhook URL (if provided)
    if webhook_url:
        return await _send_via_webhook(webhook_url, text)

    # Method 2: Bot API (using configured URL and OAuth token)
    if settings.cliq_bot_api_url and settings.cliq_auth_token:
        return await _send_via_bot_api(text, channel_id)

    # No Cliq configured — just log
    print(f"📨 [CLIQ DISABLED] Would send to channel {channel_id}:\n{text[:200]}")
    return {"status": "skipped", "reason": "cliq not configured"}


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


async def _send_via_bot_api(text: str, channel_id: str = "") -> dict:
    """
    Send message via Zoho Cliq Bot API.

    API: POST https://cliq.zoho.com/api/v2/channelsbyname/{channel}/message
    Headers: Authorization: Zoho-oauthtoken {token}
    Body: {"text": "message"}
    """
    url = settings.cliq_bot_api_url
    headers = {
        "Authorization": f"Zoho-oauthtoken {settings.cliq_auth_token}",
        "Content-Type": "application/json",
    }

    # Cliq supports rich cards — format as a card for better readability
    payload = {
        "text": text,
        "bot": {"name": "Saturn"},
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            return {"status": "sent", "method": "bot_api"}
        except httpx.HTTPError as e:
            print(f"⚠️ Cliq Bot API error: {e}")
            return {"status": "error", "error": str(e)}


def format_cliq_card(
    title: str,
    summary: str,
    pr_url: str = "",
    files_changed: list[str] | None = None,
    test_passed: bool = False,
    duration: float = 0.0,
) -> dict:
    """
    Format a rich Cliq message card for task completion reports.

    Returns a dict that can be sent as the Cliq message body.
    """
    status_emoji = "✅" if test_passed else "⚠️"

    sections = [
        f"**{title}**\n",
        f"📝 {summary[:400]}",
    ]

    if pr_url:
        sections.append(f"\n🔗 **Pull Request:** {pr_url}")

    if files_changed:
        file_list = "\n".join(f"  • `{f}`" for f in files_changed[:8])
        if len(files_changed) > 8:
            file_list += f"\n  ... and {len(files_changed) - 8} more"
        sections.append(f"\n📁 **Files Changed:**\n{file_list}")

    sections.append(
        f"\n{status_emoji} Tests: {'Passed' if test_passed else 'Not verified'} "
        f"| ⏱️ {duration:.0f}s"
    )

    return {"text": "\n".join(sections)}

