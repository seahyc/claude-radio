"""Lightweight aiohttp webhook receiver server.

Runs alongside the Telegram bot on a separate port, receives POST
webhooks from GitHub Actions, Vercel, Netlify, etc., and forwards
formatted events to Telegram.
"""

import asyncio
import json
from typing import Any, Dict, Optional

import structlog
from aiohttp import web
from telegram import Bot

from . import github
from .formatter import format_event
from .router import WebhookRouter

logger = structlog.get_logger()


class WebhookServer:
    """Async HTTP server for receiving webhook notifications."""

    def __init__(
        self,
        bot: Bot,
        router: WebhookRouter,
        port: int = 9090,
        webhook_secret: Optional[str] = None,
        github_secret: Optional[str] = None,
    ):
        self.bot = bot
        self.router = router
        self.port = port
        self.webhook_secret = webhook_secret
        self.github_secret = github_secret
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        """Start the webhook HTTP server."""
        self._app = web.Application()
        self._app.router.add_post("/webhook/github", self._handle_github)
        self._app.router.add_post("/webhook/generic", self._handle_generic)
        self._app.router.add_get("/health", self._handle_health)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self.port)
        await site.start()

        logger.info("Webhook server started", port=self.port)

    async def stop(self) -> None:
        """Stop the webhook HTTP server."""
        if self._runner:
            await self._runner.cleanup()
            logger.info("Webhook server stopped")

    async def _handle_github(self, request: web.Request) -> web.Response:
        """Handle GitHub webhook POST."""
        body = await request.read()

        # Verify signature if secret is configured
        if self.github_secret:
            signature = request.headers.get("X-Hub-Signature-256", "")
            if not github.verify_signature(body, signature, self.github_secret):
                logger.warning("Invalid GitHub webhook signature")
                return web.Response(status=401, text="Invalid signature")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return web.Response(status=400, text="Invalid JSON")

        event_type = request.headers.get("X-GitHub-Event", "unknown")
        event = github.parse_event(event_type, payload)

        logger.info(
            "GitHub webhook received",
            event_type=event_type,
            repo=event.get("repo", ""),
            title=event.get("title", ""),
        )

        # Format and send to Telegram
        await self._dispatch_event(event)

        return web.Response(status=200, text="OK")

    async def _handle_generic(self, request: web.Request) -> web.Response:
        """Handle generic webhook POST (any JSON payload)."""
        # Verify secret if configured
        if self.webhook_secret:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {self.webhook_secret}":
                return web.Response(status=401, text="Unauthorized")

        try:
            payload = json.loads(await request.read())
        except json.JSONDecodeError:
            return web.Response(status=400, text="Invalid JSON")

        # Generic event format
        event = {
            "type": payload.get("type", "generic"),
            "title": payload.get("title", "Webhook Notification"),
            "description": payload.get("description", payload.get("message", "")),
            "repo": payload.get("repo", payload.get("project", "")),
            "url": payload.get("url", ""),
        }

        logger.info("Generic webhook received", title=event["title"])
        await self._dispatch_event(event)

        return web.Response(status=200, text="OK")

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({"status": "ok"})

    async def _dispatch_event(self, event: Dict[str, Any]) -> None:
        """Format event and send to all matching Telegram targets."""
        message_text, keyboard = format_event(event)
        targets = self.router.get_targets(event)

        for user_id, chat_id in targets:
            try:
                await self.bot.send_message(
                    chat_id=chat_id,
                    text=message_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard,
                )
            except Exception as e:
                logger.error(
                    "Failed to send webhook notification",
                    chat_id=chat_id,
                    error=str(e),
                )
