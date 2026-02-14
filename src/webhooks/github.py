"""GitHub webhook event parser with signature verification."""

import hashlib
import hmac
from typing import Any, Dict, Optional, Tuple


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature (HMAC-SHA256).

    Args:
        payload: Raw request body
        signature: X-Hub-Signature-256 header value
        secret: Webhook secret configured in GitHub

    Returns:
        True if signature is valid
    """
    if not signature.startswith("sha256="):
        return False

    expected = hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    received = signature[7:]  # Strip "sha256=" prefix

    return hmac.compare_digest(expected, received)


def parse_event(event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a GitHub webhook event into a normalized format.

    Args:
        event_type: GitHub event type from X-GitHub-Event header
        payload: Parsed JSON payload

    Returns:
        Normalized event dict with type, title, description, url, etc.
    """
    parsers = {
        "push": _parse_push,
        "pull_request": _parse_pull_request,
        "issues": _parse_issue,
        "check_run": _parse_check_run,
        "check_suite": _parse_check_suite,
        "workflow_run": _parse_workflow_run,
        "deployment_status": _parse_deployment_status,
    }

    parser = parsers.get(event_type)
    if parser:
        return parser(payload)

    # Generic fallback
    return {
        "type": event_type,
        "title": f"GitHub event: {event_type}",
        "description": "",
        "repo": payload.get("repository", {}).get("full_name", "unknown"),
        "url": "",
        "raw": payload,
    }


def _parse_push(payload: Dict) -> Dict:
    repo = payload.get("repository", {}).get("full_name", "")
    ref = payload.get("ref", "").replace("refs/heads/", "")
    commits = payload.get("commits", [])
    pusher = payload.get("pusher", {}).get("name", "unknown")

    commit_messages = [c.get("message", "").split("\n")[0] for c in commits[:3]]
    desc = "\n".join(f"• {msg}" for msg in commit_messages)

    return {
        "type": "push",
        "title": f"🔀 Push to {repo}/{ref}",
        "description": f"By {pusher}, {len(commits)} commit(s):\n{desc}",
        "repo": repo,
        "branch": ref,
        "url": payload.get("compare", ""),
        "commits": len(commits),
    }


def _parse_pull_request(payload: Dict) -> Dict:
    action = payload.get("action", "")
    pr = payload.get("pull_request", {})
    repo = payload.get("repository", {}).get("full_name", "")

    return {
        "type": "pull_request",
        "action": action,
        "title": f"📋 PR {action}: {pr.get('title', '')}",
        "description": f"#{pr.get('number', '')} by {pr.get('user', {}).get('login', '')}",
        "repo": repo,
        "url": pr.get("html_url", ""),
        "pr_number": pr.get("number"),
        "state": pr.get("state"),
    }


def _parse_issue(payload: Dict) -> Dict:
    action = payload.get("action", "")
    issue = payload.get("issue", {})
    repo = payload.get("repository", {}).get("full_name", "")

    return {
        "type": "issue",
        "action": action,
        "title": f"🐛 Issue {action}: {issue.get('title', '')}",
        "description": f"#{issue.get('number', '')} by {issue.get('user', {}).get('login', '')}",
        "repo": repo,
        "url": issue.get("html_url", ""),
    }


def _parse_check_run(payload: Dict) -> Dict:
    check_run = payload.get("check_run", {})
    conclusion = check_run.get("conclusion", "pending")
    name = check_run.get("name", "unknown")
    repo = payload.get("repository", {}).get("full_name", "")

    emoji = {"success": "✅", "failure": "❌", "cancelled": "🚫"}.get(conclusion, "🔄")

    return {
        "type": "check_run",
        "title": f"{emoji} Check: {name} — {conclusion}",
        "description": check_run.get("output", {}).get("summary", "")[:200],
        "repo": repo,
        "url": check_run.get("html_url", ""),
        "conclusion": conclusion,
        "name": name,
    }


def _parse_check_suite(payload: Dict) -> Dict:
    suite = payload.get("check_suite", {})
    conclusion = suite.get("conclusion", "pending")
    repo = payload.get("repository", {}).get("full_name", "")
    branch = suite.get("head_branch", "unknown")

    emoji = {"success": "✅", "failure": "❌", "cancelled": "🚫"}.get(conclusion, "🔄")

    return {
        "type": "check_suite",
        "title": f"{emoji} CI: {repo}/{branch} — {conclusion}",
        "description": f"{len(suite.get('pull_requests', []))} PR(s) affected",
        "repo": repo,
        "branch": branch,
        "url": "",
        "conclusion": conclusion,
    }


def _parse_workflow_run(payload: Dict) -> Dict:
    run = payload.get("workflow_run", {})
    conclusion = run.get("conclusion", "in_progress")
    name = run.get("name", "unknown")
    repo = payload.get("repository", {}).get("full_name", "")
    branch = run.get("head_branch", "unknown")

    emoji = {"success": "✅", "failure": "❌", "cancelled": "🚫"}.get(conclusion, "🔄")

    return {
        "type": "workflow_run",
        "title": f"{emoji} Workflow: {name} — {conclusion}",
        "description": f"Branch: {branch}",
        "repo": repo,
        "branch": branch,
        "url": run.get("html_url", ""),
        "conclusion": conclusion,
        "name": name,
    }


def _parse_deployment_status(payload: Dict) -> Dict:
    status = payload.get("deployment_status", {})
    state = status.get("state", "unknown")
    env = status.get("environment", "unknown")
    repo = payload.get("repository", {}).get("full_name", "")

    emoji = {"success": "🚀", "failure": "❌", "error": "⚠️"}.get(state, "🔄")

    return {
        "type": "deployment",
        "title": f"{emoji} Deploy to {env}: {state}",
        "description": status.get("description", "")[:200],
        "repo": repo,
        "environment": env,
        "url": status.get("target_url", ""),
        "state": state,
    }
