# Claude Radio 📻

**Voice-first multi-agent command center for Claude Code, delivered through Telegram.**

Manage multiple Claude Code agents from your phone. Give voice commands while walking the dog. Get audio briefings through your AirPods. Approve diffs with a tap. Your AI dev team, on your frequency.

## The Workflow

```
You're walking the dog. AirPods in.

Voice note arrives:
  "Quick update. Agent 1 is 67% through the auth refactor.
   Agent 2 just finished the integration tests — three files
   changed, looks clean. You need to approve that one.
   Also, CI failed on main, looks like a type error."

You hold the mic button and ramble:
  "yeah approve agent two, and for agent one the rate limiting
   is broken too, and spin up a new agent to fix that CI thing"

Bot processes:
  Interpreted 3 actions:
  * Approve Agent 2's changes -> commit & push
  * Agent 1: "Also fix rate limiting"
  * New Agent 3: "Fix CI failure on main"
  [Execute All] [Edit] [Read Back] [Cancel]

You tap [Execute All]. Keep walking.
```

## Features

### Multi-Agent Process Manager
Run multiple Claude Code sessions concurrently, each working on a different task.

```
/run "refactor auth middleware"     -> Agent 1 starts
/run "write integration tests"     -> Agent 2 starts
/agents                            -> see all running agents
/stop 2                            -> cancel Agent 2
/agent 1 "also handle edge cases"  -> direct Agent 1
/dash                              -> full command center view
```

Each agent gets a live-updating Telegram message showing progress. When done, you get action buttons: approve, view diff, instruct further, retry.

### Voice-First Interface
The primary interaction is **voice in, voice out**. Text is the fallback.

**Voice input** pipeline:
1. Send a Telegram voice note (ramble freely)
2. Local Whisper transcription (faster-whisper, runs on CPU)
3. **Chief of Staff** LLM interprets your intent using full context — active agents, project state, codebase structure
4. Structured brief with per-agent routing, shown for confirmation
5. If anything's unclear, it asks a clarifying question instead of guessing

**Voice output** pipeline:
- Agent completions, CI notifications, status updates arrive as voice notes
- A separate LLM pass rewrites responses for audio consumption (no code blocks, natural speech)
- Local TTS via Kokoro (82M params, sub-300ms, Apache 2.0)
- Every voice message has a text fallback below it for code/diffs

Toggle with `/voice on|off|auto`.

### Approval Workflow
When an agent finishes editing files:

1. Detect changes via `git diff`
2. Show compact summary: files changed, lines added/removed
3. Action buttons: `[Approve & Commit]` `[Full Diff]` `[Reject]` `[Instruct Further]`
4. On approve: auto-commit with generated message, optionally push
5. Paginated diff viewer for detailed review

### Webhook Notifications
Push events from GitHub, CI/CD, etc. into Telegram.

- GitHub webhook signature verification (HMAC-SHA256)
- Parses: push, PR, issues, check runs, workflow runs, deployments
- Formatted notifications with action buttons
- **The killer combo:** CI fails -> notification -> tap `[Fix It]` -> agent spawns to fix it -> approve diff -> auto-push

### Dashboard
One-glance view of everything.

```
/dash

Command Center

api-service (main, 2 ahead)
  Agent 1: "Refactoring auth" — 67%
  Agent 2: "Writing tests" — 40%

web-app (main, clean)
  Agent 3: Done — awaiting approval

Today: 3 agents, $1.23, 47 messages
1 pending approval

[View Agents] [Notifications] [Projects]
```

## Quick Start

### Prerequisites

- Python 3.10-3.12 (Kokoro TTS requires <3.13)
- [Poetry](https://python-poetry.org/)
- [Claude Code CLI](https://claude.ai/code) (authenticated)
- Telegram bot token from [@BotFather](https://t.me/botfather)

### Install

```bash
git clone https://github.com/seahyc/claude-radio.git
cd claude-radio

# Core dependencies
poetry install

# With voice support (local Whisper + Kokoro TTS)
poetry install --extras voice

# Everything
poetry install --extras all
```

### Configure

```bash
cp .env.example .env
```

**Minimum config:**
```bash
TELEGRAM_BOT_TOKEN=your-token-here
TELEGRAM_BOT_USERNAME=your_bot_name
APPROVED_DIRECTORY=/path/to/your/projects
ALLOWED_USERS=your-telegram-user-id
```

**For voice + agents:**
```bash
VOICE_MODE=on                    # on, off, auto
TTS_ENGINE=kokoro                # kokoro (local) or openai (API)
MAX_CONCURRENT_AGENTS=5
```

**For webhook notifications:**
```bash
WEBHOOK_NOTIFICATIONS_PORT=9090
GITHUB_WEBHOOK_SECRET=your-github-secret
```

### Run

```bash
# Development
make run-debug

# Production
make run
```

### Finding Your Telegram User ID

Message [@userinfobot](https://t.me/userinfobot) on Telegram — it replies with your ID.

## Architecture

```
Telegram <-> Bot Core <-> Agent Process Manager <-> Claude SDK (concurrent sessions)
                |                    |
                |              Progress Monitor (in-place message editing)
                |
           Voice Pipeline
           |           |
     Whisper STT    Kokoro TTS
           |
     Chief of Staff (Sonnet — intent parsing with full context)
                |
           Webhook Server (aiohttp, port 9090)
           |           |
     GitHub Parser   Generic Parser
```

**Key files:**
- `src/agents/manager.py` — Multi-agent process manager (spawn, track, stop, direct)
- `src/agents/monitor.py` — In-place Telegram message editing for agent progress
- `src/bot/features/voice_pipeline.py` — Voice orchestration (both directions)
- `src/bot/features/chief_of_staff.py` — Intent interpretation with deep context injection
- `src/webhooks/server.py` — Inbound webhook receiver
- `src/bot/features/approval_workflow.py` — Diff review + commit/push

## Commands

| Command | Description |
|---------|-------------|
| `/run <task>` | Spawn a new agent on a task |
| `/agents` | List all agents (active + recent) |
| `/agent <id> <msg>` | Direct a follow-up to an agent |
| `/stop <id\|all>` | Stop an agent or all agents |
| `/dash` | Command center dashboard |
| `/voice on\|off\|auto` | Toggle voice mode |
| `/new` | Start fresh session in current project |
| `/ls` / `/cd` / `/pwd` | Navigate projects |
| `/git` | Git repository info |
| `/help` | Show all commands |

## Configuration Reference

See [`.env.example`](.env.example) for all settings with descriptions. Key groups:

- **Bot**: Token, username, allowed users
- **Security**: Approved directory, rate limits
- **Claude**: Model, timeout, max cost, allowed/disallowed tools
- **Agents**: Max concurrent agents (1-20)
- **Voice**: Mode, TTS engine, voice name, proactive briefings
- **Webhooks**: Port, secrets for GitHub and generic providers
- **Storage**: SQLite database, session timeout

## License

MIT — see [LICENSE](LICENSE).

---

**Your AI dev team. On your frequency.**
