"""Route webhook events to the right Telegram user/chat."""

from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger()


class WebhookRouter:
    """Routes inbound webhook events to Telegram users.

    Simple mapping: repo -> list of (user_id, chat_id) pairs.
    For a solo user, everything goes to one chat.
    """

    def __init__(self, default_chat_id: Optional[int] = None):
        self.default_chat_id = default_chat_id
        # repo_name -> [(user_id, chat_id)]
        self._routes: Dict[str, List[tuple]] = {}

    def add_route(self, repo: str, user_id: int, chat_id: int) -> None:
        """Register a repo -> user/chat mapping."""
        self._routes.setdefault(repo, []).append((user_id, chat_id))

    def get_targets(self, event: Dict[str, Any]) -> List[tuple]:
        """Get (user_id, chat_id) pairs for an event.

        Falls back to default_chat_id if no specific route matches.
        """
        repo = event.get("repo", "")
        targets = self._routes.get(repo, [])

        if not targets and self.default_chat_id:
            # Broadcast to default chat for any unrouted event
            targets = [(0, self.default_chat_id)]

        return targets
