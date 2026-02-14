"""Chief of Staff LLM — interprets rambling voice notes into structured agent briefs.

Uses Claude Sonnet via the Anthropic SDK to parse ambiguous, stream-of-consciousness
voice input into clear, actionable instructions routed to the right agents.
"""

import json
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()

CHIEF_OF_STAFF_PROMPT = """You are the user's Chief of Staff for software development.
You receive disjointed, stream-of-consciousness voice transcriptions and
structure them into clear, actionable briefs.

== ACTIVE AGENTS ==
{agents_context}

== CURRENT PROJECT ==
{project_context}

== USER'S VOICE NOTE (raw transcription) ==
"{transcription}"

== YOUR TASK ==
1. Parse the user's intent — they may reference things vaguely
   ("that auth thing", "the broken part", "agent one") — resolve using context above
2. Structure into per-agent instructions, each a clear brief
3. If it's a new task (not directed at any existing agent), mark it as "new_agent"
4. IMPORTANT: If the transcription is genuinely unclear, garbled, or you cannot
   confidently determine what the user wants, set "needs_clarification" to true
   and include a clear, specific question in "clarification_question".
   Do NOT guess — ask the user. Be specific about what's unclear.
5. If you're mostly confident but there's a small ambiguity, proceed with
   the most likely interpretation and note it in "ambiguities".

Respond with ONLY a JSON object in this format:
{{
  "needs_clarification": false,
  "clarification_question": null,
  "actions": [
    {{
      "type": "direct_agent",
      "agent_id": 1,
      "message": "Clear instruction for the agent"
    }},
    {{
      "type": "new_agent",
      "task": "Task description for a new agent to work on"
    }},
    {{
      "type": "stop_agent",
      "agent_id": 2
    }},
    {{
      "type": "approve_agent",
      "agent_id": 3
    }}
  ],
  "summary": "Brief human-readable summary of what was interpreted",
  "ambiguities": ["Any unclear points, if any"]
}}

If needs_clarification is true, actions should be empty and
clarification_question should contain a concise question for the user.
"""


async def interpret_voice_input(
    transcription: str,
    agents: List[Dict[str, Any]],
    project_path: str,
    anthropic_api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Interpret a voice transcription into structured agent commands.

    Uses Claude Sonnet to parse ambiguous voice input with full context
    about running agents and the current project.

    Args:
        transcription: Raw text from Whisper transcription
        agents: List of agent info dicts with id, task, status, last_activity
        project_path: Current project directory
        anthropic_api_key: Anthropic API key (uses env var if not provided)

    Returns:
        Dict with 'actions', 'summary', and 'ambiguities' keys
    """
    # Build context strings
    if agents:
        agents_lines = []
        for a in agents:
            status = a.get("status", "unknown")
            task = a.get("task", "unknown task")
            activity = a.get("last_activity", "")
            agents_lines.append(
                f"- Agent {a['id']} ({a.get('project', 'unknown')}): "
                f"\"{task}\" — {status}"
                f"{f' | Last: {activity}' if activity else ''}"
            )
        agents_context = "\n".join(agents_lines)
    else:
        agents_context = "(No agents currently running)"

    from pathlib import Path

    project_name = Path(project_path).name
    project_context = f"{project_name} ({project_path})"

    prompt = CHIEF_OF_STAFF_PROMPT.format(
        agents_context=agents_context,
        project_context=project_context,
        transcription=transcription,
    )

    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=anthropic_api_key)

        response = await client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract text content
        text = response.content[0].text.strip()

        # Parse JSON from response (handle markdown code blocks)
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = json.loads(text)

        logger.info(
            "Voice input interpreted",
            num_actions=len(result.get("actions", [])),
            summary=result.get("summary", "")[:100],
        )
        return result

    except ImportError:
        logger.error("anthropic package required. Install with: pip install anthropic")
        return {
            "actions": [],
            "summary": "Error: anthropic package not installed",
            "ambiguities": [],
        }
    except json.JSONDecodeError as e:
        logger.error("Failed to parse Chief of Staff response", error=str(e))
        # Fallback: treat the whole transcription as a new agent task
        return {
            "actions": [{"type": "new_agent", "task": transcription}],
            "summary": f"Could not parse structured intent. Treating as new task: {transcription[:100]}",
            "ambiguities": ["Could not parse structured intent from voice input"],
        }
    except Exception as e:
        logger.error("Chief of Staff interpretation failed", error=str(e))
        return {
            "actions": [{"type": "new_agent", "task": transcription}],
            "summary": f"Interpretation failed, treating as new task: {transcription[:100]}",
            "ambiguities": [str(e)],
        }


def format_brief(interpretation: Dict[str, Any]) -> str:
    """Format the Chief of Staff's interpretation as a Telegram message.

    Returns Markdown-formatted text with the structured brief.
    """
    # Handle clarification requests
    if interpretation.get("needs_clarification"):
        question = interpretation.get("clarification_question", "Could you repeat that?")
        return (
            f"🤔 *I need some clarification:*\n\n"
            f"{question}\n\n"
            f"_Please send another voice message or type your response._"
        )

    actions = interpretation.get("actions", [])
    summary = interpretation.get("summary", "")
    ambiguities = interpretation.get("ambiguities", [])

    if not actions:
        return f"🤔 Could not interpret voice input.\n\n_{summary}_"

    lines = [f"📋 *Interpreted {len(actions)} action(s):*\n"]

    for action in actions:
        action_type = action.get("type", "unknown")

        if action_type == "direct_agent":
            agent_id = action.get("agent_id", "?")
            message = action.get("message", "")
            lines.append(f"→ *Agent {agent_id}:*\n  \"{message}\"\n")

        elif action_type == "new_agent":
            task = action.get("task", "")
            lines.append(f"🆕 *New Agent:*\n  \"{task}\"\n")

        elif action_type == "stop_agent":
            agent_id = action.get("agent_id", "?")
            lines.append(f"🛑 *Stop Agent {agent_id}*\n")

        elif action_type == "approve_agent":
            agent_id = action.get("agent_id", "?")
            lines.append(f"✅ *Approve Agent {agent_id}*\n")

    if ambiguities:
        lines.append("⚠️ *Unclear:*")
        for amb in ambiguities:
            lines.append(f"  • {amb}")

    return "\n".join(lines)
