"""Command handlers for the multi-agent system.

Commands:
  /run <task>     — Spawn a new agent
  /agents         — List all agents
  /stop <id|all>  — Stop agent(s)
  /agent <id> <msg> — Direct a message to a specific agent
  /dash           — Show command center dashboard
"""

import asyncio
from pathlib import Path

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from ...agents.manager import AgentProcessManager
from ...agents.models import AgentProcess, AgentStatus
from ...agents.monitor import AgentProgressMonitor
from ...config.settings import Settings

logger = structlog.get_logger()


async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /run <task> — spawn a new agent."""
    user_id = update.effective_user.id
    settings: Settings = context.bot_data["settings"]
    agent_manager: AgentProcessManager = context.bot_data.get("agent_manager")
    progress_monitor: AgentProgressMonitor = context.bot_data.get("progress_monitor")

    if not agent_manager:
        await update.message.reply_text("❌ Agent system not available.")
        return

    # Parse task from command args
    task = " ".join(context.args) if context.args else ""
    if not task:
        await update.message.reply_text(
            "📝 *Usage:* `/run <task description>`\n\n"
            "*Examples:*\n"
            '• `/run refactor the auth middleware`\n'
            '• `/run write tests for the user model`\n'
            '• `/run fix the CORS headers bug`',
            parse_mode="Markdown",
        )
        return

    # Get current project directory
    current_dir = context.user_data.get("current_directory", settings.approved_directory)

    try:
        # Create callbacks for the monitor
        async def on_status_update(agent: AgentProcess, activity: str):
            if progress_monitor:
                await progress_monitor.update_status(agent, activity)

        async def on_complete(agent: AgentProcess):
            if progress_monitor:
                await progress_monitor.flush_pending(agent)
                await progress_monitor.show_completion(agent)

        # Spawn the agent
        agent = await agent_manager.spawn_agent(
            user_id=user_id,
            task_description=task,
            project_path=current_dir,
            chat_id=update.effective_chat.id,
            on_status_update=on_status_update,
            on_complete=on_complete,
        )

        # Create initial status message via monitor
        if progress_monitor:
            await progress_monitor.create_status_message(agent)

        # Confirm spawn to user
        await update.message.reply_text(
            f"🚀 *Agent {agent.agent_id}* spawned\n"
            f"📋 {agent.short_task}\n"
            f"📂 `{Path(agent.project_path).name}`",
            parse_mode="Markdown",
        )

    except ValueError as e:
        await update.message.reply_text(f"⚠️ {str(e)}")
    except Exception as e:
        logger.error("Failed to spawn agent", error=str(e), user_id=user_id)
        await update.message.reply_text(f"❌ Failed to spawn agent: {str(e)}")


async def agents_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /agents — list all agents for this user."""
    user_id = update.effective_user.id
    agent_manager: AgentProcessManager = context.bot_data.get("agent_manager")

    if not agent_manager:
        await update.message.reply_text("❌ Agent system not available.")
        return

    agents = agent_manager.get_all_agents(user_id)
    if not agents:
        await update.message.reply_text(
            "🤖 *No agents*\n\n"
            "Spawn one with `/run <task>`",
            parse_mode="Markdown",
        )
        return

    lines = ["🤖 *Your Agents*\n"]

    # Group by active vs completed
    active = [a for a in agents if a.is_active]
    done = [a for a in agents if a.is_terminal]

    if active:
        lines.append("*Active:*")
        for a in active:
            elapsed = int(a.duration_seconds)
            m, s = divmod(elapsed, 60)
            activity = f" — _{a.last_activity[:60]}_" if a.last_activity else ""
            lines.append(
                f"  {a.status_emoji()} *{a.agent_id}*: {a.short_task} ({m}m{s}s){activity}"
            )
        lines.append("")

    if done:
        lines.append("*Recent:*")
        for a in sorted(done, key=lambda x: x.completed_at or x.started_at, reverse=True)[:5]:
            cost_str = f"${a.cost_usd:.3f}" if a.cost_usd else ""
            lines.append(
                f"  {a.status_emoji()} *{a.agent_id}*: {a.short_task} {cost_str}"
            )

    stats = agent_manager.get_user_stats(user_id)
    lines.append(f"\n📊 Total: {stats['total_agents']} | 💰 ${stats['total_cost']:.3f}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stop <id|all> — stop agent(s)."""
    user_id = update.effective_user.id
    agent_manager: AgentProcessManager = context.bot_data.get("agent_manager")

    if not agent_manager:
        await update.message.reply_text("❌ Agent system not available.")
        return

    if not context.args:
        await update.message.reply_text(
            "📝 *Usage:* `/stop <agent_id>` or `/stop all`",
            parse_mode="Markdown",
        )
        return

    target = context.args[0].lower()

    if target == "all":
        count = await agent_manager.stop_all_agents(user_id)
        await update.message.reply_text(f"🛑 Stopped {count} agent(s).")
    else:
        try:
            agent_id = int(target)
        except ValueError:
            await update.message.reply_text("⚠️ Agent ID must be a number or 'all'.")
            return

        success = await agent_manager.stop_agent(user_id, agent_id)
        if success:
            await update.message.reply_text(f"🛑 Agent {agent_id} stopped.")
        else:
            await update.message.reply_text(
                f"⚠️ Agent {agent_id} not found or already stopped."
            )


async def agent_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /agent <id> <message> — direct a message to a specific agent."""
    user_id = update.effective_user.id
    agent_manager: AgentProcessManager = context.bot_data.get("agent_manager")
    progress_monitor: AgentProgressMonitor = context.bot_data.get("progress_monitor")

    if not agent_manager:
        await update.message.reply_text("❌ Agent system not available.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            '📝 *Usage:* `/agent <id> <message>`\n\n'
            '*Example:*\n'
            '`/agent 1 also fix the rate limiting bug`',
            parse_mode="Markdown",
        )
        return

    try:
        agent_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("⚠️ Agent ID must be a number.")
        return

    message = " ".join(context.args[1:])

    agent = agent_manager.get_agent(user_id, agent_id)
    if not agent:
        await update.message.reply_text(f"⚠️ Agent {agent_id} not found.")
        return

    if agent.is_active:
        await update.message.reply_text(
            f"⏳ Agent {agent_id} is still running. "
            f"Wait for it to finish, then send follow-up instructions."
        )
        return

    # Callbacks for the monitor
    async def on_status_update(a: AgentProcess, activity: str):
        if progress_monitor:
            await progress_monitor.update_status(a, activity)

    async def on_complete(a: AgentProcess):
        if progress_monitor:
            await progress_monitor.flush_pending(a)
            await progress_monitor.show_completion(a)

    # Direct message to the agent (respawn with continuation)
    result = await agent_manager.direct_agent(
        user_id=user_id,
        agent_id=agent_id,
        message=message,
        on_status_update=on_status_update,
        on_complete=on_complete,
    )

    if result:
        if progress_monitor:
            await progress_monitor.create_status_message(result)
        await update.message.reply_text(
            f"💬 Directed Agent {agent_id}: _{message[:80]}_",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"⚠️ Could not direct Agent {agent_id}."
        )


async def dash_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /dash — show the command center dashboard."""
    user_id = update.effective_user.id
    settings: Settings = context.bot_data["settings"]
    agent_manager: AgentProcessManager = context.bot_data.get("agent_manager")

    if not agent_manager:
        await update.message.reply_text("❌ Agent system not available.")
        return

    agents = agent_manager.get_all_agents(user_id)
    stats = agent_manager.get_user_stats(user_id)
    current_dir = context.user_data.get("current_directory", settings.approved_directory)

    lines = ["🎛 *Command Center*\n"]

    if not agents:
        lines.append("_No agents running. Spawn one with_ `/run <task>`\n")
    else:
        # Group agents by project
        by_project: dict[str, list] = {}
        for a in agents:
            by_project.setdefault(a.project_path, []).append(a)

        for project_path, project_agents in by_project.items():
            project_name = Path(project_path).name
            lines.append(f"📁 *{project_name}*")

            for a in sorted(project_agents, key=lambda x: x.agent_id):
                if a.is_active:
                    elapsed = int(a.duration_seconds)
                    m, s = divmod(elapsed, 60)
                    activity = a.last_activity[:40] if a.last_activity else "working..."
                    lines.append(
                        f"  {a.status_emoji()} Agent {a.agent_id}: \"{a.short_task}\" — {m}m{s}s"
                    )
                elif a.status == AgentStatus.COMPLETED:
                    lines.append(
                        f"  {a.status_emoji()} Agent {a.agent_id}: Done — \"{a.short_task}\""
                    )
                elif a.status == AgentStatus.FAILED:
                    lines.append(
                        f"  {a.status_emoji()} Agent {a.agent_id}: Failed — \"{a.short_task}\""
                    )
                elif a.status == AgentStatus.AWAITING_APPROVAL:
                    lines.append(
                        f"  {a.status_emoji()} Agent {a.agent_id}: Awaiting approval"
                    )
            lines.append("")

    # Stats
    pending_approvals = len([
        a for a in agents if a.status == AgentStatus.AWAITING_APPROVAL
    ])

    lines.append(
        f"📊 Agents: {stats['active']} active, "
        f"{stats['completed']} done | "
        f"💰 ${stats['total_cost']:.2f}"
    )

    if pending_approvals:
        lines.append(f"🔔 {pending_approvals} pending approval(s)")

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    keyboard = [
        [
            InlineKeyboardButton("🤖 Agents", callback_data="action:agents_list"),
            InlineKeyboardButton("🔄 Refresh", callback_data="action:refresh_dash"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )
