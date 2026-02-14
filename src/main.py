"""Main entry point for Claude Code Telegram Bot."""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Any, Dict

import structlog

from src import __version__
from src.bot.core import ClaudeCodeBot
from src.claude import (
    ClaudeIntegration,
    ClaudeProcessManager,
    SessionManager,
    ToolMonitor,
)
from src.agents.manager import AgentProcessManager
from src.claude.sdk_integration import ClaudeSDKManager
from src.webhooks.router import WebhookRouter
from src.webhooks.server import WebhookServer
from src.config.features import FeatureFlags
from src.config.loader import load_config
from src.config.settings import Settings
from src.exceptions import ConfigurationError
from src.security.audit import AuditLogger, InMemoryAuditStorage
from src.security.auth import (
    AuthenticationManager,
    InMemoryTokenStorage,
    TokenAuthProvider,
    WhitelistAuthProvider,
)
from src.security.rate_limiter import RateLimiter
from src.security.validators import SecurityValidator
from src.storage.facade import Storage
from src.storage.session_storage import SQLiteSessionStorage


def setup_logging(debug: bool = False) -> None:
    """Configure structured logging."""
    level = logging.DEBUG if debug else logging.INFO

    # Configure standard logging
    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stdout,
    )

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            (
                structlog.processors.JSONRenderer()
                if not debug
                else structlog.dev.ConsoleRenderer()
            ),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Claude Code Telegram Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--version", action="version", version=f"Claude Code Telegram Bot {__version__}"
    )

    parser.add_argument("--debug", action="store_true", help="Enable debug logging")

    parser.add_argument("--config-file", type=Path, help="Path to configuration file")

    return parser.parse_args()


async def create_application(config: Settings) -> Dict[str, Any]:
    """Create and configure the application components."""
    logger = structlog.get_logger()
    logger.info("Creating application components")

    # Initialize storage system
    storage = Storage(config.database_url)
    await storage.initialize()

    # Create security components
    providers = []

    # Add whitelist provider if users are configured
    if config.allowed_users:
        providers.append(WhitelistAuthProvider(config.allowed_users))

    # Add token provider if enabled
    if config.enable_token_auth:
        token_storage = InMemoryTokenStorage()  # TODO: Use database storage
        providers.append(TokenAuthProvider(config.auth_token_secret, token_storage))

    # Fall back to allowing all users in development mode
    if not providers and config.development_mode:
        logger.warning(
            "No auth providers configured - creating development-only allow-all provider"
        )
        providers.append(WhitelistAuthProvider([], allow_all_dev=True))
    elif not providers:
        raise ConfigurationError("No authentication providers configured")

    auth_manager = AuthenticationManager(providers)
    security_validator = SecurityValidator(config.approved_directory)
    rate_limiter = RateLimiter(config)

    # Create audit storage and logger
    audit_storage = InMemoryAuditStorage()  # TODO: Use database storage in production
    audit_logger = AuditLogger(audit_storage)

    # Create Claude integration components with persistent storage
    session_storage = SQLiteSessionStorage(storage.db_manager)
    session_manager = SessionManager(config, session_storage)
    tool_monitor = ToolMonitor(config, security_validator)

    # Create Claude manager based on configuration
    if config.use_sdk:
        logger.info("Using Claude Python SDK integration")
        sdk_manager = ClaudeSDKManager(config)
        process_manager = None
    else:
        logger.info("Using Claude CLI subprocess integration")
        process_manager = ClaudeProcessManager(config)
        sdk_manager = None

    # Create main Claude integration facade
    claude_integration = ClaudeIntegration(
        config=config,
        process_manager=process_manager,
        sdk_manager=sdk_manager,
        session_manager=session_manager,
        tool_monitor=tool_monitor,
    )

    # Create multi-agent process manager
    agent_manager = AgentProcessManager(
        settings=config,
        claude_integration=claude_integration,
    )

    # Create bot with all dependencies
    dependencies = {
        "auth_manager": auth_manager,
        "security_validator": security_validator,
        "rate_limiter": rate_limiter,
        "audit_logger": audit_logger,
        "claude_integration": claude_integration,
        "storage": storage,
        "agent_manager": agent_manager,
    }

    bot = ClaudeCodeBot(config, dependencies)

    logger.info("Application components created successfully")

    # Webhook notification router (will be set up with bot instance later)
    webhook_router = WebhookRouter()
    # If allowed_users has a single user, set their chat as default target
    if config.allowed_users and len(config.allowed_users) == 1:
        webhook_router.default_chat_id = config.allowed_users[0]

    return {
        "bot": bot,
        "claude_integration": claude_integration,
        "agent_manager": agent_manager,
        "storage": storage,
        "config": config,
        "webhook_router": webhook_router,
    }


async def run_application(app: Dict[str, Any]) -> None:
    """Run the application with graceful shutdown handling."""
    logger = structlog.get_logger()
    bot: ClaudeCodeBot = app["bot"]
    claude_integration: ClaudeIntegration = app["claude_integration"]
    agent_manager: AgentProcessManager = app["agent_manager"]
    storage: Storage = app["storage"]

    # Set up signal handlers for graceful shutdown
    shutdown_event = asyncio.Event()

    def signal_handler(signum, frame):
        logger.info("Shutdown signal received", signal=signum)
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start webhook notification server if port is configured
    webhook_server = None
    config: Settings = app["config"]
    webhook_router: WebhookRouter = app["webhook_router"]

    try:
        # Start the bot
        logger.info("Starting Claude Code Telegram Bot")

        # Run bot in background task
        bot_task = asyncio.create_task(bot.start())
        shutdown_task = asyncio.create_task(shutdown_event.wait())

        # Start webhook server after bot is initialized (we need bot.app.bot)
        async def _start_webhook_server():
            """Start webhook server once the bot is ready."""
            # Wait for bot to initialize
            while not bot.app or not bot.app.bot:
                await asyncio.sleep(0.5)

            github_secret = (
                config.github_webhook_secret.get_secret_value()
                if config.github_webhook_secret
                else None
            )
            notif_secret = (
                config.webhook_notifications_secret.get_secret_value()
                if config.webhook_notifications_secret
                else None
            )

            nonlocal webhook_server
            webhook_server = WebhookServer(
                bot=bot.app.bot,
                router=webhook_router,
                port=config.webhook_notifications_port,
                webhook_secret=notif_secret,
                github_secret=github_secret,
            )
            await webhook_server.start()

        webhook_task = asyncio.create_task(_start_webhook_server())

        # Wait for either bot completion or shutdown signal
        done, pending = await asyncio.wait(
            [bot_task, shutdown_task, webhook_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel remaining tasks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Check if webhook task failed (log but don't crash the bot)
        if webhook_task in done and not webhook_task.cancelled():
            exc = webhook_task.exception()
            if exc:
                logger.error("Webhook server failed", error=str(exc))

    except Exception as e:
        logger.error("Application error", error=str(e))
        raise
    finally:
        # Graceful shutdown
        logger.info("Shutting down application")

        try:
            if webhook_server:
                await webhook_server.stop()
            await agent_manager.shutdown()
            await bot.stop()
            await claude_integration.shutdown()
            await storage.close()
        except Exception as e:
            logger.error("Error during shutdown", error=str(e))

        logger.info("Application shutdown complete")


async def main() -> None:
    """Main application entry point."""
    args = parse_args()
    setup_logging(debug=args.debug)

    logger = structlog.get_logger()
    logger.info("Starting Claude Code Telegram Bot", version=__version__)

    try:
        # Load configuration
        from src.config import FeatureFlags, load_config

        config = load_config(config_file=args.config_file)
        features = FeatureFlags(config)

        logger.info(
            "Configuration loaded",
            environment="production" if config.is_production else "development",
            enabled_features=features.get_enabled_features(),
            debug=config.debug,
        )

        # Initialize bot and Claude integration
        app = await create_application(config)
        await run_application(app)

    except ConfigurationError as e:
        logger.error("Configuration error", error=str(e))
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error", error=str(e))
        sys.exit(1)


def run() -> None:
    """Synchronous entry point for setuptools."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown requested by user")
        sys.exit(0)


if __name__ == "__main__":
    run()
