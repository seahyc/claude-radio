"""Agent Process Manager — spawn, track, stop, and direct concurrent Claude sessions."""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import structlog

from ..claude.facade import ClaudeIntegration
from ..claude.sdk_integration import StreamUpdate
from ..config.settings import Settings
from .models import AgentProcess, AgentStatus

logger = structlog.get_logger()


class AgentProcessManager:
    """Manages multiple concurrent Claude Code agent sessions per user."""

    def __init__(
        self,
        settings: Settings,
        claude_integration: ClaudeIntegration,
    ):
        self.settings = settings
        self.claude_integration = claude_integration
        self.max_concurrent = getattr(settings, "max_concurrent_agents", 5)

        # user_id -> {agent_id: AgentProcess}
        self._agents: Dict[int, Dict[int, AgentProcess]] = {}
        # user_id -> next agent_id counter
        self._counters: Dict[int, int] = {}
        # agent key (user_id, agent_id) -> asyncio.Task
        self._tasks: Dict[tuple, asyncio.Task] = {}
        # Lock per user for safe concurrent access
        self._locks: Dict[int, asyncio.Lock] = {}

    def _get_lock(self, user_id: int) -> asyncio.Lock:
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]

    def _next_id(self, user_id: int) -> int:
        self._counters.setdefault(user_id, 0)
        self._counters[user_id] += 1
        return self._counters[user_id]

    def get_active_agents(self, user_id: int) -> List[AgentProcess]:
        """Get all active (non-terminal) agents for a user."""
        agents = self._agents.get(user_id, {})
        return [a for a in agents.values() if a.is_active]

    def get_all_agents(self, user_id: int) -> List[AgentProcess]:
        """Get all agents for a user (including completed)."""
        return list(self._agents.get(user_id, {}).values())

    def get_agent(self, user_id: int, agent_id: int) -> Optional[AgentProcess]:
        """Get a specific agent."""
        return self._agents.get(user_id, {}).get(agent_id)

    async def spawn_agent(
        self,
        user_id: int,
        task_description: str,
        project_path: Path,
        chat_id: int,
        on_status_update: Optional[Callable] = None,
        on_complete: Optional[Callable] = None,
    ) -> AgentProcess:
        """Spawn a new agent to work on a task.

        Args:
            user_id: Telegram user ID
            task_description: What the agent should do
            project_path: Working directory for the agent
            chat_id: Telegram chat ID for status messages
            on_status_update: Callback(agent, update_text) for progress
            on_complete: Callback(agent) when done

        Returns:
            The newly created AgentProcess

        Raises:
            ValueError: If max concurrent agents reached
        """
        async with self._get_lock(user_id):
            active = self.get_active_agents(user_id)
            if len(active) >= self.max_concurrent:
                raise ValueError(
                    f"Maximum concurrent agents reached ({self.max_concurrent}). "
                    f"Stop an existing agent first with /stop <id>."
                )

            agent_id = self._next_id(user_id)
            agent = AgentProcess(
                agent_id=agent_id,
                user_id=user_id,
                session_id=None,
                project_path=str(project_path),
                task_description=task_description,
                status=AgentStatus.STARTING,
                status_message_id=None,
                chat_id=chat_id,
            )

            self._agents.setdefault(user_id, {})[agent_id] = agent

            logger.info(
                "Spawning agent",
                user_id=user_id,
                agent_id=agent_id,
                task=task_description[:80],
                project=str(project_path),
            )

        # Launch the agent task
        task = asyncio.create_task(
            self._run_agent(agent, on_status_update, on_complete)
        )
        self._tasks[(user_id, agent_id)] = task

        # Handle task cleanup
        task.add_done_callback(
            lambda t: self._on_task_done(user_id, agent_id, t)
        )

        return agent

    async def _run_agent(
        self,
        agent: AgentProcess,
        on_status_update: Optional[Callable],
        on_complete: Optional[Callable],
    ) -> None:
        """Execute the agent's task via Claude integration."""
        try:
            agent.status = AgentStatus.RUNNING

            if on_status_update:
                await on_status_update(agent, "Starting task...")

            async def stream_handler(update: StreamUpdate):
                """Handle streaming updates from Claude."""
                if update.type == "assistant" and update.content:
                    # Extract last meaningful line as activity
                    lines = update.content.strip().split("\n")
                    activity = lines[-1][:100] if lines else "Working..."
                    agent.last_activity = activity

                    if on_status_update:
                        await on_status_update(agent, activity)

                elif update.tool_calls:
                    tool_names = [tc.get("name", "unknown") for tc in update.tool_calls]
                    activity = f"Using: {', '.join(tool_names)}"
                    agent.last_activity = activity

                    if on_status_update:
                        await on_status_update(agent, activity)

            # Execute via Claude integration
            response = await self.claude_integration.run_command(
                prompt=agent.task_description,
                working_directory=Path(agent.project_path),
                user_id=agent.user_id,
                session_id=agent.session_id,
                on_stream=stream_handler,
            )

            # Update agent with results
            agent.session_id = response.session_id
            agent.cost_usd = response.cost
            agent.result_summary = response.content
            agent.completed_at = datetime.now()

            if response.is_error:
                agent.status = AgentStatus.FAILED
                agent.error_message = response.content
            else:
                agent.status = AgentStatus.COMPLETED

            logger.info(
                "Agent completed",
                agent_id=agent.agent_id,
                user_id=agent.user_id,
                status=agent.status.value,
                cost=agent.cost_usd,
                duration=agent.duration_seconds,
            )

            if on_complete:
                await on_complete(agent)

        except asyncio.CancelledError:
            agent.status = AgentStatus.STOPPED
            agent.completed_at = datetime.now()
            logger.info(
                "Agent cancelled",
                agent_id=agent.agent_id,
                user_id=agent.user_id,
            )
            raise

        except Exception as e:
            agent.status = AgentStatus.FAILED
            agent.error_message = str(e)
            agent.completed_at = datetime.now()
            logger.error(
                "Agent failed",
                agent_id=agent.agent_id,
                user_id=agent.user_id,
                error=str(e),
            )
            if on_complete:
                await on_complete(agent)

    def _on_task_done(self, user_id: int, agent_id: int, task: asyncio.Task) -> None:
        """Cleanup after task completes."""
        self._tasks.pop((user_id, agent_id), None)
        if task.cancelled():
            return
        exc = task.exception()
        if exc and not isinstance(exc, asyncio.CancelledError):
            logger.error(
                "Agent task exception",
                user_id=user_id,
                agent_id=agent_id,
                error=str(exc),
            )

    async def stop_agent(self, user_id: int, agent_id: int) -> bool:
        """Stop a running agent.

        Returns True if the agent was stopped, False if not found/already stopped.
        """
        agent = self.get_agent(user_id, agent_id)
        if not agent or not agent.is_active:
            return False

        task = self._tasks.get((user_id, agent_id))
        if task and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        agent.status = AgentStatus.STOPPED
        agent.completed_at = datetime.now()

        logger.info(
            "Agent stopped",
            user_id=user_id,
            agent_id=agent_id,
        )
        return True

    async def stop_all_agents(self, user_id: int) -> int:
        """Stop all active agents for a user. Returns count stopped."""
        active = self.get_active_agents(user_id)
        count = 0
        for agent in active:
            if await self.stop_agent(user_id, agent.agent_id):
                count += 1
        return count

    async def direct_agent(
        self,
        user_id: int,
        agent_id: int,
        message: str,
        on_status_update: Optional[Callable] = None,
        on_complete: Optional[Callable] = None,
    ) -> Optional[AgentProcess]:
        """Send a follow-up message to a running or completed agent.

        If the agent is completed, it respawns with the new message in the same session.
        """
        agent = self.get_agent(user_id, agent_id)
        if not agent:
            return None

        # If agent is still running, we can't interrupt it —
        # queue the message by spawning a continuation
        if agent.is_active:
            # For now, return None to indicate we can't direct an active agent.
            # A future enhancement could queue messages.
            return None

        # Agent is in a terminal state — respawn with the same session
        async with self._get_lock(user_id):
            # Reuse the same agent_id slot with a new task
            agent.task_description = message
            agent.status = AgentStatus.STARTING
            agent.started_at = datetime.now()
            agent.completed_at = None
            agent.result_summary = None
            agent.error_message = None
            agent.last_activity = None

        task = asyncio.create_task(
            self._run_agent(agent, on_status_update, on_complete)
        )
        self._tasks[(user_id, agent_id)] = task
        task.add_done_callback(
            lambda t: self._on_task_done(user_id, agent_id, t)
        )

        return agent

    def get_user_stats(self, user_id: int) -> Dict[str, Any]:
        """Get aggregate stats for a user's agents."""
        agents = self.get_all_agents(user_id)
        if not agents:
            return {
                "total_agents": 0,
                "active": 0,
                "completed": 0,
                "failed": 0,
                "total_cost": 0.0,
            }

        return {
            "total_agents": len(agents),
            "active": len([a for a in agents if a.is_active]),
            "completed": len([a for a in agents if a.status == AgentStatus.COMPLETED]),
            "failed": len([a for a in agents if a.status == AgentStatus.FAILED]),
            "total_cost": sum(a.cost_usd for a in agents),
        }

    async def shutdown(self) -> None:
        """Stop all agents across all users."""
        logger.info("Shutting down agent manager")
        for user_id in list(self._agents.keys()):
            await self.stop_all_agents(user_id)
        self._agents.clear()
        self._tasks.clear()
        logger.info("Agent manager shutdown complete")
