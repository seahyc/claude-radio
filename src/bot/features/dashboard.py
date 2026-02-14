"""Command center dashboard for monitoring all agents and projects."""

from pathlib import Path
from typing import Any, Dict, List

from ...agents.models import AgentProcess, AgentStatus


def format_dashboard(
    agents: List[AgentProcess],
    stats: Dict[str, Any],
    current_dir: Path,
) -> str:
    """Format the full dashboard view.

    This is a pure formatting function — the /dash command handler
    in agent_commands.py is the primary entry point. This module
    exists for reuse by audio briefings and webhook notifications.
    """
    lines = ["🎛 *Command Center*\n"]

    if not agents:
        lines.append("_No agents. Spawn one with_ `/run <task>`\n")
    else:
        by_project: dict[str, list] = {}
        for a in agents:
            by_project.setdefault(a.project_path, []).append(a)

        for project_path, project_agents in by_project.items():
            project_name = Path(project_path).name
            lines.append(f"📁 *{project_name}*")
            for a in sorted(project_agents, key=lambda x: x.agent_id):
                lines.append(f"  {_agent_line(a)}")
            lines.append("")

    pending = len([a for a in agents if a.status == AgentStatus.AWAITING_APPROVAL])
    lines.append(
        f"📊 {stats.get('active', 0)} active, "
        f"{stats.get('completed', 0)} done | "
        f"💰 ${stats.get('total_cost', 0):.2f}"
    )
    if pending:
        lines.append(f"🔔 {pending} pending approval(s)")

    return "\n".join(lines)


def format_agent_summary(agent: AgentProcess) -> str:
    """Format a single-agent summary for notifications."""
    elapsed = int(agent.duration_seconds)
    m, s = divmod(elapsed, 60)
    status_word = {
        AgentStatus.COMPLETED: "finished",
        AgentStatus.FAILED: "failed",
        AgentStatus.STOPPED: "was stopped",
        AgentStatus.RUNNING: "is running",
        AgentStatus.STARTING: "is starting",
        AgentStatus.AWAITING_APPROVAL: "needs approval",
    }.get(agent.status, agent.status.value)

    return (
        f"Agent {agent.agent_id} {status_word}. "
        f"Task: \"{agent.short_task}\". "
        f"Took {m}m {s}s, cost ${agent.cost_usd:.3f}."
    )


def _agent_line(a: AgentProcess) -> str:
    """Format one agent line for the dashboard."""
    if a.is_active:
        elapsed = int(a.duration_seconds)
        m, s = divmod(elapsed, 60)
        return f"{a.status_emoji()} Agent {a.agent_id}: \"{a.short_task}\" — {m}m{s}s"
    elif a.status == AgentStatus.COMPLETED:
        return f"{a.status_emoji()} Agent {a.agent_id}: Done — \"{a.short_task}\""
    elif a.status == AgentStatus.FAILED:
        return f"{a.status_emoji()} Agent {a.agent_id}: Failed — \"{a.short_task}\""
    elif a.status == AgentStatus.AWAITING_APPROVAL:
        return f"{a.status_emoji()} Agent {a.agent_id}: Awaiting approval"
    else:
        return f"{a.status_emoji()} Agent {a.agent_id}: {a.status.value}"
