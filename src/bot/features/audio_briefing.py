"""Audio Briefing — rewrites agent responses for audio consumption and generates voice notes."""

import re
from typing import List, Optional

import structlog

from ...agents.models import AgentProcess, AgentStatus

logger = structlog.get_logger()


def rewrite_for_audio(text: str, max_length: int = 500) -> str:
    """Rewrite a Claude response for audio consumption.

    Removes code blocks, markdown formatting, and restructures for ears:
    - Headline first, details after, action items last
    - No code blocks (those go in the text fallback)
    - Natural speech patterns
    """
    # Strip markdown formatting
    text = re.sub(r"```[\s\S]*?```", "[code block omitted]", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"#{1,6}\s+", "", text)
    text = re.sub(r"!\[.*?\]\(.*?\)", "[image]", text)
    text = re.sub(r"\[([^\]]+)\]\(.*?\)", r"\1", text)

    # Collapse multiple newlines
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Truncate to reasonable audio length
    if len(text) > max_length:
        text = text[:max_length].rsplit(" ", 1)[0] + "..."

    return text.strip()


def format_agent_audio_summary(agents: List[AgentProcess]) -> str:
    """Generate an audio-optimized summary of all agent statuses.

    This is designed to be read aloud — concise, no visual formatting,
    natural speech flow.
    """
    if not agents:
        return "No agents are running right now."

    active = [a for a in agents if a.is_active]
    completed = [a for a in agents if a.status == AgentStatus.COMPLETED]
    failed = [a for a in agents if a.status == AgentStatus.FAILED]
    pending_approval = [a for a in agents if a.status == AgentStatus.AWAITING_APPROVAL]

    parts = []

    # Quick summary
    parts.append(f"Quick update. You have {len(agents)} agents total.")

    # Active agents
    if active:
        for a in active:
            elapsed = int(a.duration_seconds)
            m, s = divmod(elapsed, 60)
            time_str = f"{m} minutes" if m else f"{s} seconds"
            activity = a.last_activity[:60] if a.last_activity else "working"
            parts.append(
                f"Agent {a.agent_id} is {time_str} into \"{a.short_task}\". "
                f"Currently {activity}."
            )

    # Completed agents
    if completed:
        for a in completed:
            parts.append(
                f"Agent {a.agent_id} finished \"{a.short_task}\". "
                f"Cost {a.cost_usd:.3f} dollars."
            )

    # Failed agents
    if failed:
        for a in failed:
            error_brief = a.error_message[:80] if a.error_message else "unknown error"
            parts.append(
                f"Agent {a.agent_id} failed on \"{a.short_task}\". "
                f"Error: {error_brief}."
            )

    # Pending approvals
    if pending_approval:
        count = len(pending_approval)
        parts.append(
            f"You have {count} pending approval{'s' if count > 1 else ''}."
        )
        for a in pending_approval:
            parts.append(f"Agent {a.agent_id} needs approval for \"{a.short_task}\".")

    return " ".join(parts)


def format_agent_completion_audio(agent: AgentProcess) -> str:
    """Format a single agent's completion for audio notification."""
    elapsed = int(agent.duration_seconds)
    m, s = divmod(elapsed, 60)

    if agent.status == AgentStatus.COMPLETED:
        summary = rewrite_for_audio(agent.result_summary or "", max_length=300)
        files_note = ""
        if agent.files_changed:
            files_note = f" {len(agent.files_changed)} files changed."

        return (
            f"Agent {agent.agent_id} just finished. "
            f"Task was: \"{agent.short_task}\". "
            f"Took {m} minutes {s} seconds, "
            f"cost {agent.cost_usd:.3f} dollars.{files_note} "
            f"{summary}"
        )

    elif agent.status == AgentStatus.FAILED:
        error = agent.error_message[:100] if agent.error_message else "unknown error"
        return (
            f"Agent {agent.agent_id} failed. "
            f"Task was: \"{agent.short_task}\". "
            f"Error: {error}."
        )

    elif agent.status == AgentStatus.STOPPED:
        return f"Agent {agent.agent_id} was stopped."

    return f"Agent {agent.agent_id} status changed to {agent.status.value}."
