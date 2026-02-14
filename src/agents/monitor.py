"""Agent Progress Monitor — in-place Telegram message editing for agent status."""

import asyncio
import time
from typing import Optional

import structlog
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, TimedOut

from .models import AgentProcess, AgentStatus

logger = structlog.get_logger()

# Minimum interval between message edits (Telegram rate limit)
MIN_EDIT_INTERVAL = 2.0


class AgentProgressMonitor:
    """Manages real-time status messages for agents in Telegram."""

    def __init__(self, bot: Bot):
        self.bot = bot
        # Track last edit time per agent to avoid rate limits
        self._last_edit: dict[tuple[int, int], float] = {}  # (user_id, agent_id) -> timestamp
        # Pending updates that haven't been sent yet due to rate limiting
        self._pending: dict[tuple[int, int], str] = {}

    async def create_status_message(self, agent: AgentProcess) -> None:
        """Create the initial status message for a new agent."""
        text = self._format_status(agent, "Starting...")

        try:
            msg = await self.bot.send_message(
                chat_id=agent.chat_id,
                text=text,
                parse_mode="Markdown",
            )
            agent.status_message_id = msg.message_id
            logger.debug(
                "Created agent status message",
                agent_id=agent.agent_id,
                message_id=msg.message_id,
            )
        except Exception as e:
            logger.error(
                "Failed to create status message",
                agent_id=agent.agent_id,
                error=str(e),
            )

    async def update_status(self, agent: AgentProcess, activity: str) -> None:
        """Update the agent's status message in-place.

        Rate-limited to avoid Telegram API throttling.
        """
        if not agent.status_message_id or not agent.chat_id:
            return

        key = (agent.user_id, agent.agent_id)
        now = time.monotonic()
        last = self._last_edit.get(key, 0)

        if now - last < MIN_EDIT_INTERVAL:
            # Store as pending — will be sent on next allowed interval
            self._pending[key] = activity
            return

        # Clear any pending update since we're sending now
        self._pending.pop(key, None)
        self._last_edit[key] = now

        text = self._format_status(agent, activity)
        await self._edit_message(agent, text)

    async def show_completion(self, agent: AgentProcess) -> None:
        """Update the status message to show completion with action buttons."""
        if not agent.status_message_id or not agent.chat_id:
            return

        # Flush any pending updates
        self._pending.pop((agent.user_id, agent.agent_id), None)

        text = self._format_completion(agent)
        keyboard = self._completion_keyboard(agent)

        await self._edit_message(agent, text, keyboard)

    async def flush_pending(self, agent: AgentProcess) -> None:
        """Send any pending status update for an agent."""
        key = (agent.user_id, agent.agent_id)
        pending = self._pending.pop(key, None)
        if pending:
            self._last_edit[key] = time.monotonic()
            text = self._format_status(agent, pending)
            await self._edit_message(agent, text)

    def _format_status(self, agent: AgentProcess, activity: str) -> str:
        """Format the running status message."""
        elapsed = int(agent.duration_seconds)
        mins, secs = divmod(elapsed, 60)

        lines = [
            f"{agent.status_emoji()} *Agent {agent.agent_id}* — {_escape_md(agent.short_task)}",
            f"📂 `{_project_name(agent.project_path)}`",
            f"⏱ {mins}m {secs}s",
        ]

        if agent.cost_usd > 0:
            lines.append(f"💰 ${agent.cost_usd:.4f}")

        lines.append(f"\n_{_escape_md(activity[:150])}_")

        return "\n".join(lines)

    def _format_completion(self, agent: AgentProcess) -> str:
        """Format the completion message."""
        elapsed = int(agent.duration_seconds)
        mins, secs = divmod(elapsed, 60)

        status_label = {
            AgentStatus.COMPLETED: "Completed",
            AgentStatus.FAILED: "Failed",
            AgentStatus.STOPPED: "Stopped",
            AgentStatus.AWAITING_APPROVAL: "Awaiting Approval",
        }.get(agent.status, agent.status.value)

        lines = [
            f"{agent.status_emoji()} *Agent {agent.agent_id}* — {status_label}",
            f"📋 {_escape_md(agent.short_task)}",
            f"📂 `{_project_name(agent.project_path)}`",
            f"⏱ {mins}m {secs}s | 💰 ${agent.cost_usd:.4f}",
        ]

        if agent.result_summary:
            # Show first 300 chars of result
            summary = agent.result_summary[:300]
            if len(agent.result_summary) > 300:
                summary += "..."
            lines.append(f"\n{_escape_md(summary)}")

        if agent.error_message:
            lines.append(f"\n❌ _{_escape_md(agent.error_message[:200])}_")

        if agent.files_changed:
            lines.append(f"\n📝 Files changed: {len(agent.files_changed)}")
            for f in agent.files_changed[:5]:
                lines.append(f"  • `{f}`")
            if len(agent.files_changed) > 5:
                lines.append(f"  ... and {len(agent.files_changed) - 5} more")

        return "\n".join(lines)

    def _completion_keyboard(self, agent: AgentProcess) -> Optional[InlineKeyboardMarkup]:
        """Build action buttons for a completed agent."""
        buttons = []

        if agent.status == AgentStatus.COMPLETED:
            buttons.append([
                InlineKeyboardButton(
                    "💬 Follow Up",
                    callback_data=f"agent_followup:{agent.user_id}:{agent.agent_id}",
                ),
                InlineKeyboardButton(
                    "📊 Full Output",
                    callback_data=f"agent_output:{agent.user_id}:{agent.agent_id}",
                ),
            ])
            buttons.append([
                InlineKeyboardButton(
                    "✅ Approve & Commit",
                    callback_data=f"agent_approve:{agent.user_id}:{agent.agent_id}",
                ),
                InlineKeyboardButton(
                    "📋 Show Diff",
                    callback_data=f"agent_diff:{agent.user_id}:{agent.agent_id}",
                ),
            ])
        elif agent.status == AgentStatus.FAILED:
            buttons.append([
                InlineKeyboardButton(
                    "🔄 Retry",
                    callback_data=f"agent_retry:{agent.user_id}:{agent.agent_id}",
                ),
                InlineKeyboardButton(
                    "📊 Error Details",
                    callback_data=f"agent_output:{agent.user_id}:{agent.agent_id}",
                ),
            ])

        return InlineKeyboardMarkup(buttons) if buttons else None

    async def _edit_message(
        self,
        agent: AgentProcess,
        text: str,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
    ) -> None:
        """Edit the agent's status message, handling errors gracefully."""
        try:
            await self.bot.edit_message_text(
                chat_id=agent.chat_id,
                message_id=agent.status_message_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
        except BadRequest as e:
            if "message is not modified" in str(e).lower():
                pass  # Same content, ignore
            else:
                logger.warning(
                    "Failed to edit agent status message",
                    agent_id=agent.agent_id,
                    error=str(e),
                )
        except TimedOut:
            logger.warning(
                "Timed out editing agent message",
                agent_id=agent.agent_id,
            )
        except Exception as e:
            logger.error(
                "Error editing agent status message",
                agent_id=agent.agent_id,
                error=str(e),
            )


def _escape_md(text: str) -> str:
    """Escape Markdown v1 special characters for safe display."""
    # Only escape chars that break Markdown v1 parsing
    for char in ["_", "*", "[", "]", "`"]:
        text = text.replace(char, f"\\{char}")
    return text


def _project_name(path: str) -> str:
    """Extract just the project name from a full path."""
    from pathlib import Path as P
    return P(path).name
