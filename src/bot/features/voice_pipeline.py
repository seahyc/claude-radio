"""Voice Pipeline — orchestrates voice input and output for the audio-first interface.

Flow:
  Voice In:  Telegram voice note → Whisper (local) → Chief of Staff (Sonnet) → Confirm → Execute
  Voice Out: Agent event → Audio rewrite → TTS (local Kokoro) → Telegram voice note
"""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

import structlog
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from ...agents.manager import AgentProcessManager
from ...agents.models import AgentProcess, AgentStatus
from ...config.settings import Settings
from . import audio_briefing, chief_of_staff, tts_engine, voice_transcription

logger = structlog.get_logger()


class VoicePipeline:
    """Orchestrates the full voice-in/voice-out pipeline."""

    def __init__(
        self,
        settings: Settings,
        bot: Bot,
        agent_manager: AgentProcessManager,
    ):
        self.settings = settings
        self.bot = bot
        self.agent_manager = agent_manager
        self.voice_mode = settings.voice_mode  # on, off, auto
        self.tts_engine_name = settings.tts_engine
        self.tts_voice = settings.tts_voice
        self._openai_key = (
            settings.openai_api_key.get_secret_value()
            if settings.openai_api_key
            else None
        )
        self._anthropic_key = settings.anthropic_api_key_str

    # ---- Voice Input Pipeline ----

    async def process_voice_input(
        self,
        audio_data: bytes,
        user_id: int,
        chat_id: int,
        context: Any = None,
    ) -> str:
        """Full voice input pipeline: transcribe → interpret → format brief.

        Returns a Markdown-formatted message with the interpreted actions
        and inline buttons for confirmation.
        """
        # Step 1: Transcribe
        transcription = await voice_transcription.transcribe_audio(audio_data)

        if not transcription.strip():
            return "🎤 Could not transcribe any speech from the voice message."

        # Step 2: Get agent context
        agents = self.agent_manager.get_all_agents(user_id)
        agent_context = [
            {
                "id": a.agent_id,
                "task": a.task_description,
                "status": a.status.value,
                "last_activity": a.last_activity or "",
                "project": Path(a.project_path).name,
            }
            for a in agents
        ]

        # Get current project path
        current_dir = str(self.settings.approved_directory)
        if context and hasattr(context, "user_data"):
            current_dir = str(
                context.user_data.get("current_directory", self.settings.approved_directory)
            )

        # Step 3: Interpret via Chief of Staff
        if agent_context or len(transcription.split()) > 10:
            # Use LLM interpretation for complex input or when agents exist
            interpretation = await chief_of_staff.interpret_voice_input(
                transcription=transcription,
                agents=agent_context,
                project_path=current_dir,
                anthropic_api_key=self._anthropic_key,
            )

            # If clarification is needed, ask the user directly
            if interpretation.get("needs_clarification"):
                question = interpretation.get(
                    "clarification_question", "Could you repeat that?"
                )
                return (
                    f"🤔 *I need some clarification:*\n\n"
                    f"{question}\n\n"
                    f"_Reply with a voice message or text._\n\n"
                    f"---\n🎤 _{transcription}_"
                )

            brief = chief_of_staff.format_brief(interpretation)
        else:
            # Simple case: no agents, short message — just echo transcription
            brief = f"🎤 *Transcription:*\n\n\"{transcription}\"\n\n_Send as text message?_"

        # Add the raw transcription at the bottom
        result = f"{brief}\n\n---\n🎤 _{transcription}_"

        return result

    # ---- Voice Output Pipeline ----

    async def send_voice_briefing(
        self,
        chat_id: int,
        text: str,
        text_fallback: Optional[str] = None,
    ) -> None:
        """Generate and send a voice note with text fallback.

        Args:
            chat_id: Telegram chat to send to
            text: Text to convert to speech (audio-optimized)
            text_fallback: Optional full text version (with code blocks, etc.)
        """
        if self.voice_mode == "off":
            # Text only
            if text_fallback:
                await self.bot.send_message(
                    chat_id=chat_id, text=text_fallback, parse_mode="Markdown"
                )
            return

        try:
            # Generate audio
            audio_bytes = await tts_engine.synthesize_speech(
                text=text,
                engine=self.tts_engine_name,
                voice=self.tts_voice,
                openai_api_key=self._openai_key,
            )

            # Send as voice note
            import io
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = "briefing.ogg"

            await self.bot.send_voice(
                chat_id=chat_id,
                voice=audio_file,
            )

            # Send text fallback below (collapsed)
            if text_fallback:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=f"📝 *Text version:*\n\n{text_fallback}",
                    parse_mode="Markdown",
                )

        except Exception as e:
            logger.error("Failed to send voice briefing", error=str(e))
            # Fall back to text
            fallback = text_fallback or text
            await self.bot.send_message(
                chat_id=chat_id, text=fallback, parse_mode="Markdown"
            )

    async def send_agent_completion_briefing(
        self,
        agent: AgentProcess,
    ) -> None:
        """Send a voice briefing when an agent completes."""
        if not self.settings.proactive_briefings:
            return
        if not agent.chat_id:
            return

        # Generate audio-optimized summary
        audio_text = audio_briefing.format_agent_completion_audio(agent)

        # Generate text fallback with full details
        text_fallback = self._format_agent_text_fallback(agent)

        await self.send_voice_briefing(
            chat_id=agent.chat_id,
            text=audio_text,
            text_fallback=text_fallback,
        )

    async def send_full_status_briefing(
        self,
        user_id: int,
        chat_id: int,
    ) -> None:
        """Send a full audio status briefing of all agents."""
        agents = self.agent_manager.get_all_agents(user_id)
        audio_text = audio_briefing.format_agent_audio_summary(agents)

        # Text fallback is the standard dashboard
        from .dashboard import format_dashboard
        stats = self.agent_manager.get_user_stats(user_id)
        text_fallback = format_dashboard(
            agents,
            stats,
            self.settings.approved_directory,
        )

        await self.send_voice_briefing(
            chat_id=chat_id,
            text=audio_text,
            text_fallback=text_fallback,
        )

    def _format_agent_text_fallback(self, agent: AgentProcess) -> str:
        """Format a text fallback for an agent completion notification."""
        lines = [
            f"{agent.status_emoji()} *Agent {agent.agent_id}* — {agent.status.value}",
            f"📋 {agent.short_task}",
            f"💰 ${agent.cost_usd:.4f} | ⏱ {int(agent.duration_seconds)}s",
        ]

        if agent.result_summary:
            summary = agent.result_summary[:500]
            if len(agent.result_summary) > 500:
                summary += "..."
            lines.append(f"\n{summary}")

        if agent.files_changed:
            lines.append(f"\n📝 Files: {', '.join(agent.files_changed[:5])}")

        if agent.error_message:
            lines.append(f"\n❌ {agent.error_message[:200]}")

        return "\n".join(lines)
