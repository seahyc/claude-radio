"""Data models for the multi-agent system."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


class AgentStatus(str, Enum):
    """Agent lifecycle states."""

    STARTING = "starting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    AWAITING_APPROVAL = "awaiting_approval"
    STOPPED = "stopped"


@dataclass
class AgentProcess:
    """Represents a running Claude Code agent."""

    agent_id: int  # Sequential per user (1, 2, 3...)
    user_id: int  # Telegram user ID
    session_id: Optional[str]  # Claude session ID (set after first response)
    project_path: str  # Working directory
    task_description: str  # What it's working on
    status: AgentStatus  # Current lifecycle state
    status_message_id: Optional[int]  # Telegram message ID being edited with progress
    chat_id: Optional[int]  # Telegram chat ID for the status message
    started_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    result_summary: Optional[str] = None
    files_changed: List[str] = field(default_factory=list)
    cost_usd: float = 0.0
    last_activity: Optional[str] = None  # Last thing the agent did
    error_message: Optional[str] = None

    @property
    def is_active(self) -> bool:
        """Check if agent is still running."""
        return self.status in (AgentStatus.STARTING, AgentStatus.RUNNING)

    @property
    def is_terminal(self) -> bool:
        """Check if agent has reached a final state."""
        return self.status in (
            AgentStatus.COMPLETED,
            AgentStatus.FAILED,
            AgentStatus.STOPPED,
        )

    @property
    def duration_seconds(self) -> float:
        """Get elapsed time in seconds."""
        end = self.completed_at or datetime.now()
        return (end - self.started_at).total_seconds()

    @property
    def short_task(self) -> str:
        """Get truncated task description for display."""
        if len(self.task_description) <= 50:
            return self.task_description
        return self.task_description[:47] + "..."

    def status_emoji(self) -> str:
        """Get emoji for current status."""
        return {
            AgentStatus.STARTING: "🔄",
            AgentStatus.RUNNING: "🔄",
            AgentStatus.COMPLETED: "✅",
            AgentStatus.FAILED: "❌",
            AgentStatus.AWAITING_APPROVAL: "🔔",
            AgentStatus.STOPPED: "🛑",
        }.get(self.status, "❓")
