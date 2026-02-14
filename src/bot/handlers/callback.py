"""Handle inline keyboard callbacks."""

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ...claude.facade import ClaudeIntegration
from ...config.settings import Settings
from ...security.audit import AuditLogger
from ...security.validators import SecurityValidator

logger = structlog.get_logger()


async def handle_callback_query(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Route callback queries to appropriate handlers."""
    query = update.callback_query
    await query.answer()  # Acknowledge the callback

    user_id = query.from_user.id
    data = query.data

    logger.info("Processing callback query", user_id=user_id, callback_data=data)

    try:
        # Parse callback data
        if ":" in data:
            action, param = data.split(":", 1)
        else:
            action, param = data, None

        # Route to appropriate handler
        handlers = {
            "cd": handle_cd_callback,
            "action": handle_action_callback,
            "confirm": handle_confirm_callback,
            "quick": handle_quick_action_callback,
            "followup": handle_followup_callback,
            "conversation": handle_conversation_callback,
            "git": handle_git_callback,
            "export": handle_export_callback,
            "agent_approve": handle_agent_approve_callback,
            "agent_diff": handle_agent_diff_callback,
            "agent_diff_page": handle_agent_diff_page_callback,
            "agent_diff_close": handle_agent_diff_close_callback,
            "agent_reject": handle_agent_reject_callback,
            "agent_followup": handle_agent_followup_callback,
            "agent_output": handle_agent_output_callback,
            "agent_retry": handle_agent_retry_callback,
            "agent_push": handle_agent_push_callback,
        }

        handler = handlers.get(action)
        if handler:
            await handler(query, param, context)
        else:
            await query.edit_message_text(
                "❌ **Unknown Action**\n\n"
                "This button action is not recognized. "
                "The bot may have been updated since this message was sent."
            )

    except Exception as e:
        logger.error(
            "Error handling callback query",
            error=str(e),
            user_id=user_id,
            callback_data=data,
        )

        try:
            await query.edit_message_text(
                "❌ **Error Processing Action**\n\n"
                "An error occurred while processing your request.\n"
                "Please try again or use text commands."
            )
        except Exception:
            # If we can't edit the message, send a new one
            await query.message.reply_text(
                "❌ **Error Processing Action**\n\n"
                "An error occurred while processing your request."
            )


async def handle_cd_callback(
    query, project_name: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle directory change from inline keyboard."""
    user_id = query.from_user.id
    settings: Settings = context.bot_data["settings"]
    security_validator: SecurityValidator = context.bot_data.get("security_validator")
    audit_logger: AuditLogger = context.bot_data.get("audit_logger")

    try:
        current_dir = context.user_data.get(
            "current_directory", settings.approved_directory
        )

        # Handle special paths
        if project_name == "/":
            new_path = settings.approved_directory
        elif project_name == "..":
            new_path = current_dir.parent
            # Ensure we don't go above approved directory
            if not str(new_path).startswith(str(settings.approved_directory)):
                new_path = settings.approved_directory
        else:
            new_path = settings.approved_directory / project_name

        # Validate path if security validator is available
        if security_validator:
            # Pass the absolute path for validation
            valid, resolved_path, error = security_validator.validate_path(
                str(new_path), settings.approved_directory
            )
            if not valid:
                await query.edit_message_text(f"❌ **Access Denied**\n\n{error}")
                return
            # Use the validated path
            new_path = resolved_path

        # Check if directory exists
        if not new_path.exists() or not new_path.is_dir():
            await query.edit_message_text(
                f"❌ **Directory Not Found**\n\n"
                f"The directory `{project_name}` no longer exists or is not accessible."
            )
            return

        # Update directory and clear session
        context.user_data["current_directory"] = new_path
        context.user_data["claude_session_id"] = None

        # Send confirmation with new directory info
        relative_path = new_path.relative_to(settings.approved_directory)

        # Add navigation buttons
        keyboard = [
            [
                InlineKeyboardButton("📁 List Files", callback_data="action:ls"),
                InlineKeyboardButton(
                    "🆕 New Session", callback_data="action:new_session"
                ),
            ],
            [
                InlineKeyboardButton(
                    "📋 Projects", callback_data="action:show_projects"
                ),
                InlineKeyboardButton("📊 Status", callback_data="action:status"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"✅ **Directory Changed**\n\n"
            f"📂 Current directory: `{relative_path}/`\n\n"
            f"🔄 Claude session cleared. You can now start coding in this directory!",
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )

        # Log successful directory change
        if audit_logger:
            await audit_logger.log_command(
                user_id=user_id, command="cd", args=[project_name], success=True
            )

    except Exception as e:
        await query.edit_message_text(f"❌ **Error changing directory**\n\n{str(e)}")

        if audit_logger:
            await audit_logger.log_command(
                user_id=user_id, command="cd", args=[project_name], success=False
            )


async def handle_action_callback(
    query, action_type: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle general action callbacks."""
    actions = {
        "help": _handle_help_action,
        "show_projects": _handle_show_projects_action,
        "new_session": _handle_new_session_action,
        "continue": _handle_continue_action,
        "end_session": _handle_end_session_action,
        "status": _handle_status_action,
        "ls": _handle_ls_action,
        "start_coding": _handle_start_coding_action,
        "quick_actions": _handle_quick_actions_action,
        "refresh_status": _handle_refresh_status_action,
        "refresh_ls": _handle_refresh_ls_action,
        "export": _handle_export_action,
        "refresh_dash": _handle_refresh_dash_action,
        "agents_list": _handle_agents_list_action,
    }

    handler = actions.get(action_type)
    if handler:
        await handler(query, context)
    else:
        await query.edit_message_text(
            f"❌ **Unknown Action: {action_type}**\n\n"
            "This action is not implemented yet."
        )


async def handle_confirm_callback(
    query, confirmation_type: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle confirmation dialogs."""
    if confirmation_type == "yes":
        await query.edit_message_text("✅ **Confirmed**\n\nAction will be processed.")
    elif confirmation_type == "no":
        await query.edit_message_text("❌ **Cancelled**\n\nAction was cancelled.")
    else:
        await query.edit_message_text("❓ **Unknown confirmation response**")


# Action handlers


async def _handle_help_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle help action."""
    help_text = (
        "🤖 **Quick Help**\n\n"
        "**Navigation:**\n"
        "• `/ls` - List files\n"
        "• `/cd <dir>` - Change directory\n"
        "• `/projects` - Show projects\n\n"
        "**Sessions:**\n"
        "• `/new` - New Claude session\n"
        "• `/status` - Session status\n\n"
        "**Tips:**\n"
        "• Send any text to interact with Claude\n"
        "• Upload files for code review\n"
        "• Use buttons for quick actions\n\n"
        "Use `/help` for detailed help."
    )

    keyboard = [
        [
            InlineKeyboardButton("📖 Full Help", callback_data="action:full_help"),
            InlineKeyboardButton("🏠 Main Menu", callback_data="action:main_menu"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        help_text, parse_mode="Markdown", reply_markup=reply_markup
    )


async def _handle_show_projects_action(
    query, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle show projects action."""
    settings: Settings = context.bot_data["settings"]

    try:
        # Get directories in approved directory
        projects = []
        for item in sorted(settings.approved_directory.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                projects.append(item.name)

        if not projects:
            await query.edit_message_text(
                "📁 **No Projects Found**\n\n"
                "No subdirectories found in your approved directory.\n"
                "Create some directories to organize your projects!"
            )
            return

        # Create project buttons
        keyboard = []
        for i in range(0, len(projects), 2):
            row = []
            for j in range(2):
                if i + j < len(projects):
                    project = projects[i + j]
                    row.append(
                        InlineKeyboardButton(
                            f"📁 {project}", callback_data=f"cd:{project}"
                        )
                    )
            keyboard.append(row)

        # Add navigation buttons
        keyboard.append(
            [
                InlineKeyboardButton("🏠 Root", callback_data="cd:/"),
                InlineKeyboardButton(
                    "🔄 Refresh", callback_data="action:show_projects"
                ),
            ]
        )

        reply_markup = InlineKeyboardMarkup(keyboard)
        project_list = "\n".join([f"• `{project}/`" for project in projects])

        await query.edit_message_text(
            f"📁 **Available Projects**\n\n"
            f"{project_list}\n\n"
            f"Click a project to navigate to it:",
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )

    except Exception as e:
        await query.edit_message_text(f"❌ Error loading projects: {str(e)}")


async def _handle_new_session_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle new session action."""
    settings: Settings = context.bot_data["settings"]

    # Clear session
    context.user_data["claude_session_id"] = None
    context.user_data["session_started"] = True

    current_dir = context.user_data.get(
        "current_directory", settings.approved_directory
    )
    relative_path = current_dir.relative_to(settings.approved_directory)

    keyboard = [
        [
            InlineKeyboardButton(
                "📝 Start Coding", callback_data="action:start_coding"
            ),
            InlineKeyboardButton(
                "📁 Change Project", callback_data="action:show_projects"
            ),
        ],
        [
            InlineKeyboardButton(
                "📋 Quick Actions", callback_data="action:quick_actions"
            ),
            InlineKeyboardButton("❓ Help", callback_data="action:help"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"🆕 **New Claude Code Session**\n\n"
        f"📂 Working directory: `{relative_path}/`\n\n"
        f"Ready to help you code! Send me a message to get started:",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def _handle_end_session_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle end session action."""
    settings: Settings = context.bot_data["settings"]

    # Check if there's an active session
    claude_session_id = context.user_data.get("claude_session_id")

    if not claude_session_id:
        await query.edit_message_text(
            "ℹ️ **No Active Session**\n\n"
            "There's no active Claude session to end.\n\n"
            "**What you can do:**\n"
            "• Use the button below to start a new session\n"
            "• Check your session status\n"
            "• Send any message to start a conversation",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🆕 New Session", callback_data="action:new_session"
                        )
                    ],
                    [InlineKeyboardButton("📊 Status", callback_data="action:status")],
                ]
            ),
        )
        return

    # Get current directory for display
    current_dir = context.user_data.get(
        "current_directory", settings.approved_directory
    )
    relative_path = current_dir.relative_to(settings.approved_directory)

    # Clear session data
    context.user_data["claude_session_id"] = None
    context.user_data["session_started"] = False
    context.user_data["last_message"] = None

    # Create quick action buttons
    keyboard = [
        [
            InlineKeyboardButton("🆕 New Session", callback_data="action:new_session"),
            InlineKeyboardButton(
                "📁 Change Project", callback_data="action:show_projects"
            ),
        ],
        [
            InlineKeyboardButton("📊 Status", callback_data="action:status"),
            InlineKeyboardButton("❓ Help", callback_data="action:help"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "✅ **Session Ended**\n\n"
        f"Your Claude session has been terminated.\n\n"
        f"**Current Status:**\n"
        f"• Directory: `{relative_path}/`\n"
        f"• Session: None\n"
        f"• Ready for new commands\n\n"
        f"**Next Steps:**\n"
        f"• Start a new session\n"
        f"• Check status\n"
        f"• Send any message to begin a new conversation",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def _handle_continue_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle continue session action."""
    user_id = query.from_user.id
    settings: Settings = context.bot_data["settings"]
    claude_integration: ClaudeIntegration = context.bot_data.get("claude_integration")

    current_dir = context.user_data.get(
        "current_directory", settings.approved_directory
    )

    try:
        if not claude_integration:
            await query.edit_message_text(
                "❌ **Claude Integration Not Available**\n\n"
                "Claude integration is not properly configured."
            )
            return

        # Check if there's an existing session in user context
        claude_session_id = context.user_data.get("claude_session_id")

        if claude_session_id:
            # Continue with the existing session (no prompt = use --continue)
            await query.edit_message_text(
                f"🔄 **Continuing Session**\n\n"
                f"Session ID: `{claude_session_id[:8]}...`\n"
                f"Directory: `{current_dir.relative_to(settings.approved_directory)}/`\n\n"
                f"Continuing where you left off...",
                parse_mode="Markdown",
            )

            claude_response = await claude_integration.run_command(
                prompt="",  # Empty prompt triggers --continue
                working_directory=current_dir,
                user_id=user_id,
                session_id=claude_session_id,
            )
        else:
            # No session in context, try to find the most recent session
            await query.edit_message_text(
                "🔍 **Looking for Recent Session**\n\n"
                "Searching for your most recent session in this directory...",
                parse_mode="Markdown",
            )

            claude_response = await claude_integration.continue_session(
                user_id=user_id,
                working_directory=current_dir,
                prompt=None,  # No prompt = use --continue
            )

        if claude_response:
            # Update session ID in context
            context.user_data["claude_session_id"] = claude_response.session_id

            # Send Claude's response
            await query.message.reply_text(
                f"✅ **Session Continued**\n\n"
                f"{claude_response.content[:500]}{'...' if len(claude_response.content) > 500 else ''}",
                parse_mode="Markdown",
            )
        else:
            # No session found to continue
            await query.edit_message_text(
                "❌ **No Session Found**\n\n"
                f"No recent Claude session found in this directory.\n"
                f"Directory: `{current_dir.relative_to(settings.approved_directory)}/`\n\n"
                f"**What you can do:**\n"
                f"• Use the button below to start a fresh session\n"
                f"• Check your session status\n"
                f"• Navigate to a different directory",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "🆕 New Session", callback_data="action:new_session"
                            ),
                            InlineKeyboardButton(
                                "📊 Status", callback_data="action:status"
                            ),
                        ]
                    ]
                ),
            )

    except Exception as e:
        logger.error("Error in continue action", error=str(e), user_id=user_id)
        await query.edit_message_text(
            f"❌ **Error Continuing Session**\n\n"
            f"An error occurred: `{str(e)}`\n\n"
            f"Try starting a new session instead.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "🆕 New Session", callback_data="action:new_session"
                        )
                    ]
                ]
            ),
        )


async def _handle_status_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle status action."""
    # This essentially duplicates the /status command functionality
    user_id = query.from_user.id
    settings: Settings = context.bot_data["settings"]

    claude_session_id = context.user_data.get("claude_session_id")
    current_dir = context.user_data.get(
        "current_directory", settings.approved_directory
    )
    relative_path = current_dir.relative_to(settings.approved_directory)

    # Get usage info if rate limiter is available
    rate_limiter = context.bot_data.get("rate_limiter")
    usage_info = ""
    if rate_limiter:
        try:
            user_status = rate_limiter.get_user_status(user_id)
            cost_usage = user_status.get("cost_usage", {})
            current_cost = cost_usage.get("current", 0.0)
            cost_limit = cost_usage.get("limit", settings.claude_max_cost_per_user)
            cost_percentage = (current_cost / cost_limit) * 100 if cost_limit > 0 else 0

            usage_info = f"💰 Usage: ${current_cost:.2f} / ${cost_limit:.2f} ({cost_percentage:.0f}%)\n"
        except Exception:
            usage_info = "💰 Usage: _Unable to retrieve_\n"

    status_lines = [
        "📊 **Session Status**",
        "",
        f"📂 Directory: `{relative_path}/`",
        f"🤖 Claude Session: {'✅ Active' if claude_session_id else '❌ None'}",
        usage_info.rstrip(),
    ]

    if claude_session_id:
        status_lines.append(f"🆔 Session ID: `{claude_session_id[:8]}...`")

    # Add action buttons
    keyboard = []
    if claude_session_id:
        keyboard.append(
            [
                InlineKeyboardButton("🔄 Continue", callback_data="action:continue"),
                InlineKeyboardButton(
                    "🛑 End Session", callback_data="action:end_session"
                ),
            ]
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    "🆕 New Session", callback_data="action:new_session"
                ),
            ]
        )
    else:
        keyboard.append(
            [
                InlineKeyboardButton(
                    "🆕 Start Session", callback_data="action:new_session"
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton("🔄 Refresh", callback_data="action:refresh_status"),
            InlineKeyboardButton("📁 Projects", callback_data="action:show_projects"),
        ]
    )

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "\n".join(status_lines), parse_mode="Markdown", reply_markup=reply_markup
    )


async def _handle_ls_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle ls action."""
    settings: Settings = context.bot_data["settings"]
    current_dir = context.user_data.get(
        "current_directory", settings.approved_directory
    )

    try:
        # List directory contents (similar to /ls command)
        items = []
        directories = []
        files = []

        for item in sorted(current_dir.iterdir()):
            if item.name.startswith("."):
                continue

            if item.is_dir():
                directories.append(f"📁 {item.name}/")
            else:
                try:
                    size = item.stat().st_size
                    size_str = _format_file_size(size)
                    files.append(f"📄 {item.name} ({size_str})")
                except OSError:
                    files.append(f"📄 {item.name}")

        items = directories + files
        relative_path = current_dir.relative_to(settings.approved_directory)

        if not items:
            message = f"📂 `{relative_path}/`\n\n_(empty directory)_"
        else:
            message = f"📂 `{relative_path}/`\n\n"
            max_items = 30  # Limit for inline display
            if len(items) > max_items:
                shown_items = items[:max_items]
                message += "\n".join(shown_items)
                message += f"\n\n_... and {len(items) - max_items} more items_"
            else:
                message += "\n".join(items)

        # Add buttons
        keyboard = []
        if current_dir != settings.approved_directory:
            keyboard.append(
                [
                    InlineKeyboardButton("⬆️ Go Up", callback_data="cd:.."),
                    InlineKeyboardButton("🏠 Root", callback_data="cd:/"),
                ]
            )

        keyboard.append(
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="action:refresh_ls"),
                InlineKeyboardButton(
                    "📋 Projects", callback_data="action:show_projects"
                ),
            ]
        )

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            message, parse_mode="Markdown", reply_markup=reply_markup
        )

    except Exception as e:
        await query.edit_message_text(f"❌ Error listing directory: {str(e)}")


async def _handle_start_coding_action(
    query, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle start coding action."""
    await query.edit_message_text(
        "🚀 **Ready to Code!**\n\n"
        "Send me any message to start coding with Claude:\n\n"
        "**Examples:**\n"
        '• _"Create a Python script that..."_\n'
        '• _"Help me debug this code..."_\n'
        '• _"Explain how this file works..."_\n'
        "• Upload a file for review\n\n"
        "I'm here to help with all your coding needs!"
    )


async def _handle_quick_actions_action(
    query, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle quick actions menu."""
    keyboard = [
        [
            InlineKeyboardButton("🧪 Run Tests", callback_data="quick:test"),
            InlineKeyboardButton("📦 Install Deps", callback_data="quick:install"),
        ],
        [
            InlineKeyboardButton("🎨 Format Code", callback_data="quick:format"),
            InlineKeyboardButton("🔍 Find TODOs", callback_data="quick:find_todos"),
        ],
        [
            InlineKeyboardButton("🔨 Build", callback_data="quick:build"),
            InlineKeyboardButton("🚀 Start Server", callback_data="quick:start"),
        ],
        [
            InlineKeyboardButton("📊 Git Status", callback_data="quick:git_status"),
            InlineKeyboardButton("🔧 Lint Code", callback_data="quick:lint"),
        ],
        [InlineKeyboardButton("⬅️ Back", callback_data="action:new_session")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "🛠️ **Quick Actions**\n\n"
        "Choose a common development task:\n\n"
        "_Note: These will be fully functional once Claude Code integration is complete._",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def _handle_refresh_status_action(
    query, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle refresh status action."""
    await _handle_status_action(query, context)


async def _handle_refresh_ls_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle refresh ls action."""
    await _handle_ls_action(query, context)


async def _handle_refresh_dash_action(
    query, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle refresh dashboard action."""
    user_id = query.from_user.id
    settings: Settings = context.bot_data["settings"]
    agent_manager = context.bot_data.get("agent_manager")

    if not agent_manager:
        await query.edit_message_text("❌ Agent system not available.")
        return

    from ..features.dashboard import format_dashboard

    agents = agent_manager.get_all_agents(user_id)
    stats = agent_manager.get_user_stats(user_id)
    current_dir = context.user_data.get(
        "current_directory", settings.approved_directory
    )

    text = format_dashboard(agents, stats, current_dir)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🤖 Agents", callback_data="action:agents_list"),
            InlineKeyboardButton("🔄 Refresh", callback_data="action:refresh_dash"),
        ],
    ])

    await query.edit_message_text(
        text, parse_mode="Markdown", reply_markup=keyboard
    )


async def _handle_agents_list_action(
    query, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle agents list action (from dashboard button)."""
    user_id = query.from_user.id
    agent_manager = context.bot_data.get("agent_manager")

    if not agent_manager:
        await query.edit_message_text("❌ Agent system not available.")
        return

    agents = agent_manager.get_all_agents(user_id)
    if not agents:
        await query.edit_message_text(
            "🤖 *No agents*\n\nSpawn one with `/run <task>`",
            parse_mode="Markdown",
        )
        return

    lines = ["🤖 *Your Agents*\n"]
    for a in agents:
        elapsed = int(a.duration_seconds)
        m, s = divmod(elapsed, 60)
        lines.append(f"{a.status_emoji()} *{a.agent_id}*: {a.short_task} ({m}m{s}s)")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎛 Dashboard", callback_data="action:refresh_dash"),
            InlineKeyboardButton("🔄 Refresh", callback_data="action:agents_list"),
        ],
    ])

    await query.edit_message_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=keyboard
    )


async def _handle_export_action(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle export action."""
    await query.edit_message_text(
        "📤 **Export Session**\n\n"
        "Session export functionality will be available once the storage layer is implemented.\n\n"
        "**Planned features:**\n"
        "• Export conversation history\n"
        "• Save session state\n"
        "• Share conversations\n"
        "• Create session backups\n\n"
        "_Coming in the next development phase!_"
    )


async def handle_quick_action_callback(
    query, action_id: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle quick action callbacks."""
    user_id = query.from_user.id

    # Get quick actions manager from bot data if available
    quick_actions = context.bot_data.get("quick_actions")

    if not quick_actions:
        await query.edit_message_text(
            "❌ **Quick Actions Not Available**\n\n"
            "Quick actions feature is not available."
        )
        return

    # Get Claude integration
    claude_integration: ClaudeIntegration = context.bot_data.get("claude_integration")
    if not claude_integration:
        await query.edit_message_text(
            "❌ **Claude Integration Not Available**\n\n"
            "Claude integration is not properly configured."
        )
        return

    settings: Settings = context.bot_data["settings"]
    current_dir = context.user_data.get(
        "current_directory", settings.approved_directory
    )

    try:
        # Get the action from the manager
        action = quick_actions.actions.get(action_id)
        if not action:
            await query.edit_message_text(
                f"❌ **Action Not Found**\n\n"
                f"Quick action '{action_id}' is not available."
            )
            return

        # Execute the action
        await query.edit_message_text(
            f"🚀 **Executing {action.icon} {action.name}**\n\n"
            f"Running quick action in directory: `{current_dir.relative_to(settings.approved_directory)}/`\n\n"
            f"Please wait...",
            parse_mode="Markdown",
        )

        # Run the action through Claude
        claude_response = await claude_integration.run_command(
            prompt=action.prompt, working_directory=current_dir, user_id=user_id
        )

        if claude_response:
            # Format and send the response
            response_text = claude_response.content
            if len(response_text) > 4000:
                response_text = response_text[:4000] + "...\n\n_(Response truncated)_"

            await query.message.reply_text(
                f"✅ **{action.icon} {action.name} Complete**\n\n{response_text}",
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(
                f"❌ **Action Failed**\n\n"
                f"Failed to execute {action.name}. Please try again."
            )

    except Exception as e:
        logger.error("Quick action execution failed", error=str(e), user_id=user_id)
        await query.edit_message_text(
            f"❌ **Action Error**\n\n"
            f"An error occurred while executing {action_id}: {str(e)}"
        )


async def handle_followup_callback(
    query, suggestion_hash: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle follow-up suggestion callbacks."""
    user_id = query.from_user.id

    # Get conversation enhancer from bot data if available
    conversation_enhancer = context.bot_data.get("conversation_enhancer")

    if not conversation_enhancer:
        await query.edit_message_text(
            "❌ **Follow-up Not Available**\n\n"
            "Conversation enhancement features are not available."
        )
        return

    try:
        # Get stored suggestions (this would need to be implemented in the enhancer)
        # For now, we'll provide a generic response
        await query.edit_message_text(
            "💡 **Follow-up Suggestion Selected**\n\n"
            "This follow-up suggestion will be implemented once the conversation "
            "enhancement system is fully integrated with the message handler.\n\n"
            "**Current Status:**\n"
            "• Suggestion received ✅\n"
            "• Integration pending 🔄\n\n"
            "_You can continue the conversation by sending a new message._"
        )

        logger.info(
            "Follow-up suggestion selected",
            user_id=user_id,
            suggestion_hash=suggestion_hash,
        )

    except Exception as e:
        logger.error(
            "Error handling follow-up callback",
            error=str(e),
            user_id=user_id,
            suggestion_hash=suggestion_hash,
        )

        await query.edit_message_text(
            "❌ **Error Processing Follow-up**\n\n"
            "An error occurred while processing your follow-up suggestion."
        )


async def handle_conversation_callback(
    query, action_type: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle conversation control callbacks."""
    user_id = query.from_user.id
    settings: Settings = context.bot_data["settings"]

    if action_type == "continue":
        # Remove suggestion buttons and show continue message
        await query.edit_message_text(
            "✅ **Continuing Conversation**\n\n"
            "Send me your next message to continue coding!\n\n"
            "I'm ready to help with:\n"
            "• Code review and debugging\n"
            "• Feature implementation\n"
            "• Architecture decisions\n"
            "• Testing and optimization\n"
            "• Documentation\n\n"
            "_Just type your request or upload files._"
        )

    elif action_type == "end":
        # End the current session
        conversation_enhancer = context.bot_data.get("conversation_enhancer")
        if conversation_enhancer:
            conversation_enhancer.clear_context(user_id)

        # Clear session data
        context.user_data["claude_session_id"] = None
        context.user_data["session_started"] = False

        current_dir = context.user_data.get(
            "current_directory", settings.approved_directory
        )
        relative_path = current_dir.relative_to(settings.approved_directory)

        # Create quick action buttons
        keyboard = [
            [
                InlineKeyboardButton(
                    "🆕 New Session", callback_data="action:new_session"
                ),
                InlineKeyboardButton(
                    "📁 Change Project", callback_data="action:show_projects"
                ),
            ],
            [
                InlineKeyboardButton("📊 Status", callback_data="action:status"),
                InlineKeyboardButton("❓ Help", callback_data="action:help"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            "✅ **Conversation Ended**\n\n"
            f"Your Claude session has been terminated.\n\n"
            f"**Current Status:**\n"
            f"• Directory: `{relative_path}/`\n"
            f"• Session: None\n"
            f"• Ready for new commands\n\n"
            f"**Next Steps:**\n"
            f"• Start a new session\n"
            f"• Check status\n"
            f"• Send any message to begin a new conversation",
            parse_mode="Markdown",
            reply_markup=reply_markup,
        )

        logger.info("Conversation ended via callback", user_id=user_id)

    else:
        await query.edit_message_text(
            f"❌ **Unknown Conversation Action: {action_type}**\n\n"
            "This conversation action is not recognized."
        )


async def handle_git_callback(
    query, git_action: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle git-related callbacks."""
    user_id = query.from_user.id
    settings: Settings = context.bot_data["settings"]
    features = context.bot_data.get("features")

    if not features or not features.is_enabled("git"):
        await query.edit_message_text(
            "❌ **Git Integration Disabled**\n\n"
            "Git integration feature is not enabled."
        )
        return

    current_dir = context.user_data.get(
        "current_directory", settings.approved_directory
    )

    try:
        git_integration = features.get_git_integration()
        if not git_integration:
            await query.edit_message_text(
                "❌ **Git Integration Unavailable**\n\n"
                "Git integration service is not available."
            )
            return

        if git_action == "status":
            # Refresh git status
            git_status = await git_integration.get_status(current_dir)
            status_message = git_integration.format_status(git_status)

            keyboard = [
                [
                    InlineKeyboardButton("📊 Show Diff", callback_data="git:diff"),
                    InlineKeyboardButton("📜 Show Log", callback_data="git:log"),
                ],
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="git:status"),
                    InlineKeyboardButton("📁 Files", callback_data="action:ls"),
                ],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                status_message, parse_mode="Markdown", reply_markup=reply_markup
            )

        elif git_action == "diff":
            # Show git diff
            diff_output = await git_integration.get_diff(current_dir)

            if not diff_output.strip():
                diff_message = "📊 **Git Diff**\n\n_No changes to show._"
            else:
                # Clean up diff output for Telegram
                # Remove emoji symbols that interfere with markdown parsing
                clean_diff = diff_output.replace("➕", "+").replace("➖", "-").replace("📍", "@")
                
                # Limit diff output
                max_length = 2000
                if len(clean_diff) > max_length:
                    clean_diff = (
                        clean_diff[:max_length] + "\n\n_... output truncated ..._"
                    )

                diff_message = f"📊 **Git Diff**\n\n```\n{clean_diff}\n```"

            keyboard = [
                [
                    InlineKeyboardButton("📜 Show Log", callback_data="git:log"),
                    InlineKeyboardButton("📊 Status", callback_data="git:status"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                diff_message, parse_mode="Markdown", reply_markup=reply_markup
            )

        elif git_action == "log":
            # Show git log
            commits = await git_integration.get_file_history(current_dir, ".")

            if not commits:
                log_message = "📜 **Git Log**\n\n_No commits found._"
            else:
                log_message = "📜 **Git Log**\n\n"
                for commit in commits[:10]:  # Show last 10 commits
                    short_hash = commit.hash[:7]
                    short_message = commit.message[:60]
                    if len(commit.message) > 60:
                        short_message += "..."
                    log_message += f"• `{short_hash}` {short_message}\n"

            keyboard = [
                [
                    InlineKeyboardButton("📊 Show Diff", callback_data="git:diff"),
                    InlineKeyboardButton("📊 Status", callback_data="git:status"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                log_message, parse_mode="Markdown", reply_markup=reply_markup
            )

        else:
            await query.edit_message_text(
                f"❌ **Unknown Git Action: {git_action}**\n\n"
                "This git action is not recognized."
            )

    except Exception as e:
        logger.error(
            "Error in git callback",
            error=str(e),
            git_action=git_action,
            user_id=user_id,
        )
        await query.edit_message_text(f"❌ **Git Error**\n\n{str(e)}")


async def handle_export_callback(
    query, export_format: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle export format selection callbacks."""
    user_id = query.from_user.id
    features = context.bot_data.get("features")

    if export_format == "cancel":
        await query.edit_message_text(
            "📤 **Export Cancelled**\n\n" "Session export has been cancelled."
        )
        return

    session_exporter = features.get_session_export() if features else None
    if not session_exporter:
        await query.edit_message_text(
            "❌ **Export Unavailable**\n\n" "Session export service is not available."
        )
        return

    # Get current session
    claude_session_id = context.user_data.get("claude_session_id")
    if not claude_session_id:
        await query.edit_message_text(
            "❌ **No Active Session**\n\n" "There's no active session to export."
        )
        return

    try:
        # Show processing message
        await query.edit_message_text(
            f"📤 **Exporting Session**\n\n"
            f"Generating {export_format.upper()} export...",
            parse_mode="Markdown",
        )

        # Export session
        exported_session = await session_exporter.export_session(
            claude_session_id, export_format
        )

        # Send the exported file
        from io import BytesIO

        file_bytes = BytesIO(exported_session.content.encode("utf-8"))
        file_bytes.name = exported_session.filename

        await query.message.reply_document(
            document=file_bytes,
            filename=exported_session.filename,
            caption=(
                f"📤 **Session Export Complete**\n\n"
                f"Format: {exported_session.format.upper()}\n"
                f"Size: {exported_session.size_bytes:,} bytes\n"
                f"Created: {exported_session.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
            ),
            parse_mode="Markdown",
        )

        # Update the original message
        await query.edit_message_text(
            f"✅ **Export Complete**\n\n"
            f"Your session has been exported as {exported_session.filename}.\n"
            f"Check the file above for your complete conversation history.",
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.error(
            "Export failed", error=str(e), user_id=user_id, format=export_format
        )
        await query.edit_message_text(f"❌ **Export Failed**\n\n{str(e)}")


async def handle_agent_approve_callback(
    query, param: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle agent approval — commit changes."""
    parts = param.split(":")
    if len(parts) < 2:
        await query.edit_message_text("❌ Invalid callback data")
        return

    agent_user_id, agent_id = int(parts[0]), int(parts[1])
    agent_manager = context.bot_data.get("agent_manager")

    if not agent_manager:
        await query.edit_message_text("❌ Agent system not available.")
        return

    agent = agent_manager.get_agent(agent_user_id, agent_id)
    if not agent:
        await query.edit_message_text(f"❌ Agent {agent_id} not found.")
        return

    from ..features.approval_workflow import commit_changes

    await query.edit_message_text(f"⏳ Committing Agent {agent_id}'s changes...")

    commit_msg = f"Agent {agent_id}: {agent.task_description[:60]}"
    success, output = await commit_changes(agent.project_path, commit_msg)

    if success:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🚀 Push",
                callback_data=f"agent_push:{agent_user_id}:{agent_id}",
            )]
        ])
        await query.edit_message_text(
            f"✅ *Committed*\n\n{output[:500]}",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    else:
        await query.edit_message_text(f"❌ Commit failed:\n{output[:500]}")


async def handle_agent_diff_callback(
    query, param: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle showing the full diff for an agent."""
    parts = param.split(":")
    if len(parts) < 2:
        await query.edit_message_text("❌ Invalid callback data")
        return

    agent_user_id, agent_id = int(parts[0]), int(parts[1])
    agent_manager = context.bot_data.get("agent_manager")

    if not agent_manager:
        await query.edit_message_text("❌ Agent system not available.")
        return

    agent = agent_manager.get_agent(agent_user_id, agent_id)
    if not agent:
        await query.edit_message_text(f"❌ Agent {agent_id} not found.")
        return

    from ..features.approval_workflow import get_file_diff
    from ..features.diff_viewer import diff_navigation_keyboard, paginate_diff

    import asyncio
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--no-color",
        cwd=agent.project_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    diff_text = stdout.decode()

    if not diff_text.strip():
        await query.edit_message_text("📊 No unstaged changes to show.")
        return

    page_text, has_prev, has_next = paginate_diff(diff_text, page=0)
    keyboard = diff_navigation_keyboard(agent_user_id, agent_id, 0, has_prev, has_next)

    await query.edit_message_text(
        page_text,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def handle_agent_diff_page_callback(
    query, param: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle diff page navigation."""
    parts = param.split(":")
    if len(parts) < 3:
        await query.edit_message_text("❌ Invalid callback data")
        return

    agent_user_id, agent_id, page = int(parts[0]), int(parts[1]), int(parts[2])
    agent_manager = context.bot_data.get("agent_manager")

    agent = agent_manager.get_agent(agent_user_id, agent_id) if agent_manager else None
    if not agent:
        await query.edit_message_text(f"❌ Agent {agent_id} not found.")
        return

    import asyncio
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--no-color",
        cwd=agent.project_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    diff_text = stdout.decode()

    from ..features.diff_viewer import diff_navigation_keyboard, paginate_diff
    page_text, has_prev, has_next = paginate_diff(diff_text, page=page)
    keyboard = diff_navigation_keyboard(agent_user_id, agent_id, page, has_prev, has_next)

    await query.edit_message_text(
        page_text,
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def handle_agent_diff_close_callback(
    query, param: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Close the diff viewer."""
    await query.edit_message_text("📊 Diff viewer closed.")


async def handle_agent_reject_callback(
    query, param: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Reject agent changes."""
    parts = param.split(":")
    if len(parts) < 2:
        return
    agent_id = int(parts[1])
    await query.edit_message_text(
        f"❌ Agent {agent_id}'s changes rejected.\n"
        f"Use `/agent {agent_id} <instructions>` to give further directions.",
        parse_mode="Markdown",
    )


async def handle_agent_followup_callback(
    query, param: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Prompt user to send follow-up to an agent."""
    parts = param.split(":")
    if len(parts) < 2:
        return
    agent_id = int(parts[1])
    await query.edit_message_text(
        f"💬 Send a follow-up to Agent {agent_id}:\n\n"
        f"`/agent {agent_id} your message here`",
        parse_mode="Markdown",
    )


async def handle_agent_output_callback(
    query, param: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show full agent output."""
    parts = param.split(":")
    if len(parts) < 2:
        return

    agent_user_id, agent_id = int(parts[0]), int(parts[1])
    agent_manager = context.bot_data.get("agent_manager")

    agent = agent_manager.get_agent(agent_user_id, agent_id) if agent_manager else None
    if not agent:
        await query.edit_message_text(f"❌ Agent {agent_id} not found.")
        return

    output = agent.result_summary or agent.error_message or "_No output available_"
    if len(output) > 4000:
        output = output[:4000] + "\n\n_... truncated ..._"

    await query.edit_message_text(
        f"📊 *Agent {agent_id} Output*\n\n{output}",
        parse_mode="Markdown",
    )


async def handle_agent_retry_callback(
    query, param: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Retry a failed agent."""
    parts = param.split(":")
    if len(parts) < 2:
        return

    agent_user_id, agent_id = int(parts[0]), int(parts[1])
    agent_manager = context.bot_data.get("agent_manager")
    progress_monitor = context.bot_data.get("progress_monitor")

    agent = agent_manager.get_agent(agent_user_id, agent_id) if agent_manager else None
    if not agent:
        await query.edit_message_text(f"❌ Agent {agent_id} not found.")
        return

    async def on_status_update(a, activity):
        if progress_monitor:
            await progress_monitor.update_status(a, activity)

    async def on_complete(a):
        if progress_monitor:
            await progress_monitor.flush_pending(a)
            await progress_monitor.show_completion(a)

    result = await agent_manager.direct_agent(
        user_id=agent_user_id,
        agent_id=agent_id,
        message=agent.task_description,
        on_status_update=on_status_update,
        on_complete=on_complete,
    )

    if result:
        if progress_monitor:
            await progress_monitor.create_status_message(result)
        await query.edit_message_text(f"🔄 Retrying Agent {agent_id}...")
    else:
        await query.edit_message_text(f"❌ Could not retry Agent {agent_id}.")


async def handle_agent_push_callback(
    query, param: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle pushing committed agent changes to remote."""
    parts = param.split(":")
    if len(parts) < 2:
        await query.edit_message_text("❌ Invalid callback data")
        return

    agent_user_id, agent_id = int(parts[0]), int(parts[1])
    agent_manager = context.bot_data.get("agent_manager")

    agent = agent_manager.get_agent(agent_user_id, agent_id) if agent_manager else None
    if not agent:
        await query.edit_message_text(f"❌ Agent {agent_id} not found.")
        return

    from ..features.approval_workflow import push_changes

    await query.edit_message_text(f"⏳ Pushing Agent {agent_id}'s changes...")

    success, output = await push_changes(agent.project_path)

    if success:
        await query.edit_message_text(
            f"🚀 *Pushed successfully*\n\n{output[:500]}",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text(f"❌ Push failed:\n{output[:500]}")


def _format_file_size(size: int) -> str:
    """Format file size in human-readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}B"
        size /= 1024
    return f"{size:.1f}TB"
