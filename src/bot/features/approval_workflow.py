"""Approval workflow for agent results — diff detection, review, commit, push."""

import asyncio
from pathlib import Path
from typing import List, Optional, Tuple

import structlog
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from ...agents.models import AgentProcess, AgentStatus

logger = structlog.get_logger()


async def detect_changes(project_path: str) -> Tuple[str, List[str], int, int]:
    """Detect git changes in a project directory.

    Returns:
        Tuple of (diff_summary, changed_files, insertions, deletions)
    """
    path = Path(project_path)

    # Get list of changed files
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--name-only",
        cwd=path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    changed_files = [f for f in stdout.decode().strip().split("\n") if f]

    # Also get untracked files
    proc = await asyncio.create_subprocess_exec(
        "git", "ls-files", "--others", "--exclude-standard",
        cwd=path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    untracked = [f for f in stdout.decode().strip().split("\n") if f]
    changed_files.extend(untracked)

    # Get diff stat
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--stat",
        cwd=path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    diff_summary = stdout.decode().strip()

    # Get insertions/deletions count
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--shortstat",
        cwd=path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    stat_line = stdout.decode().strip()
    insertions = deletions = 0
    if "insertion" in stat_line:
        import re
        ins_match = re.search(r"(\d+) insertion", stat_line)
        del_match = re.search(r"(\d+) deletion", stat_line)
        if ins_match:
            insertions = int(ins_match.group(1))
        if del_match:
            deletions = int(del_match.group(1))

    return diff_summary, changed_files, insertions, deletions


async def get_file_diff(project_path: str, file_path: str) -> str:
    """Get the diff for a specific file."""
    proc = await asyncio.create_subprocess_exec(
        "git", "diff", "--no-color", file_path,
        cwd=project_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode()


async def commit_changes(
    project_path: str,
    message: str,
    files: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """Stage and commit changes.

    Args:
        project_path: Git repository path
        message: Commit message
        files: Specific files to stage (None = all changes)

    Returns:
        Tuple of (success, output_message)
    """
    path = Path(project_path)

    # Stage files
    if files:
        for f in files:
            proc = await asyncio.create_subprocess_exec(
                "git", "add", f,
                cwd=path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                return False, f"Failed to stage {f}: {stderr.decode()}"
    else:
        proc = await asyncio.create_subprocess_exec(
            "git", "add", "-A",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            return False, f"Failed to stage changes: {stderr.decode()}"

    # Commit
    proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", message,
        cwd=path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        return False, f"Commit failed: {stderr.decode()}"

    return True, stdout.decode().strip()


async def push_changes(
    project_path: str,
    remote: str = "origin",
    branch: Optional[str] = None,
) -> Tuple[bool, str]:
    """Push committed changes to remote.

    Args:
        project_path: Git repository path
        remote: Remote name
        branch: Branch name (None = current branch)

    Returns:
        Tuple of (success, output_message)
    """
    path = Path(project_path)

    cmd = ["git", "push", remote]
    if branch:
        cmd.append(branch)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        return False, f"Push failed: {stderr.decode()}"

    return True, (stdout.decode() + stderr.decode()).strip()


def format_approval_message(
    agent: AgentProcess,
    changed_files: List[str],
    insertions: int,
    deletions: int,
    diff_summary: str,
) -> Tuple[str, InlineKeyboardMarkup]:
    """Format the approval review message with action buttons.

    Returns:
        Tuple of (message_text, keyboard_markup)
    """
    lines = [
        f"🔔 *Agent {agent.agent_id} — Review Changes*",
        f"📋 {agent.short_task}",
        f"📂 `{Path(agent.project_path).name}`",
        "",
        f"📝 *{len(changed_files)} file(s) changed*",
        f"  +{insertions} / -{deletions} lines",
        "",
    ]

    # Show changed files (up to 10)
    for f in changed_files[:10]:
        lines.append(f"  • `{f}`")
    if len(changed_files) > 10:
        lines.append(f"  ... and {len(changed_files) - 10} more")

    if diff_summary:
        # Show compact stat
        lines.append(f"\n```\n{diff_summary[:500]}\n```")

    text = "\n".join(lines)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Approve & Commit",
                callback_data=f"agent_approve:{agent.user_id}:{agent.agent_id}",
            ),
            InlineKeyboardButton(
                "📋 Full Diff",
                callback_data=f"agent_diff:{agent.user_id}:{agent.agent_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"agent_reject:{agent.user_id}:{agent.agent_id}",
            ),
            InlineKeyboardButton(
                "💬 Instruct Further",
                callback_data=f"agent_followup:{agent.user_id}:{agent.agent_id}",
            ),
        ],
    ])

    return text, keyboard
