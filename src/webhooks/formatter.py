"""Format webhook events for Telegram display."""

from typing import Any, Dict, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def format_event(event: Dict[str, Any]) -> Tuple[str, Optional[InlineKeyboardMarkup]]:
    """Format a normalized webhook event for Telegram.

    Returns:
        Tuple of (message_text, optional_keyboard)
    """
    event_type = event.get("type", "unknown")
    title = event.get("title", "Webhook Event")
    description = event.get("description", "")
    repo = event.get("repo", "")
    url = event.get("url", "")

    lines = [f"*{title}*"]
    if repo:
        lines.append(f"📁 `{repo}`")
    if description:
        lines.append(f"\n{description}")

    buttons = []

    # Add URL button if available
    if url:
        buttons.append([InlineKeyboardButton("🔗 View", url=url)])

    # Add "Fix It" button for CI failures
    conclusion = event.get("conclusion", "")
    if conclusion in ("failure", "error") and event_type in (
        "check_run",
        "check_suite",
        "workflow_run",
    ):
        buttons.append([
            InlineKeyboardButton(
                "🔧 Fix It",
                callback_data=f"webhook_fix:{event_type}:{event.get('repo', '')}",
            )
        ])

    keyboard = InlineKeyboardMarkup(buttons) if buttons else None
    return "\n".join(lines), keyboard
