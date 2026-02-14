"""Paginated diff viewer for Telegram — browse diffs file by file."""

from typing import List, Optional, Tuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def paginate_diff(
    diff_text: str,
    page: int = 0,
    page_size: int = 3000,
) -> Tuple[str, bool, bool]:
    """Paginate a diff for Telegram's message size limit.

    Args:
        diff_text: Full diff text
        page: Current page (0-indexed)
        page_size: Max characters per page

    Returns:
        Tuple of (page_text, has_prev, has_next)
    """
    if not diff_text:
        return "_No changes_", False, False

    # Split into chunks at file boundaries if possible
    chunks = _split_at_file_boundaries(diff_text, page_size)

    if page < 0:
        page = 0
    if page >= len(chunks):
        page = len(chunks) - 1

    has_prev = page > 0
    has_next = page < len(chunks) - 1

    header = f"📊 *Diff* (page {page + 1}/{len(chunks)})\n\n"
    return header + f"```\n{chunks[page]}\n```", has_prev, has_next


def diff_navigation_keyboard(
    agent_user_id: int,
    agent_id: int,
    page: int,
    has_prev: bool,
    has_next: bool,
) -> InlineKeyboardMarkup:
    """Build navigation buttons for paginated diff."""
    nav_row = []
    if has_prev:
        nav_row.append(
            InlineKeyboardButton(
                "⬅️ Prev",
                callback_data=f"agent_diff_page:{agent_user_id}:{agent_id}:{page - 1}",
            )
        )
    if has_next:
        nav_row.append(
            InlineKeyboardButton(
                "➡️ Next",
                callback_data=f"agent_diff_page:{agent_user_id}:{agent_id}:{page + 1}",
            )
        )

    action_row = [
        InlineKeyboardButton(
            "✅ Approve",
            callback_data=f"agent_approve:{agent_user_id}:{agent_id}",
        ),
        InlineKeyboardButton(
            "❌ Close",
            callback_data=f"agent_diff_close:{agent_user_id}:{agent_id}",
        ),
    ]

    rows = []
    if nav_row:
        rows.append(nav_row)
    rows.append(action_row)

    return InlineKeyboardMarkup(rows)


def _split_at_file_boundaries(text: str, max_size: int) -> List[str]:
    """Split diff text at file boundaries (diff --git lines), respecting max_size."""
    if len(text) <= max_size:
        return [text]

    # Split by file headers
    import re
    parts = re.split(r"(?=^diff --git )", text, flags=re.MULTILINE)
    parts = [p for p in parts if p.strip()]

    chunks = []
    current_chunk = ""

    for part in parts:
        if len(current_chunk) + len(part) > max_size:
            if current_chunk:
                chunks.append(current_chunk)
            # If a single file diff is too big, split it further
            if len(part) > max_size:
                for i in range(0, len(part), max_size):
                    chunks.append(part[i : i + max_size])
                current_chunk = ""
            else:
                current_chunk = part
        else:
            current_chunk += part

    if current_chunk:
        chunks.append(current_chunk)

    return chunks if chunks else ["_No diff content_"]
