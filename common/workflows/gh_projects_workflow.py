"""GitHub Projects agentic workflow."""

from __future__ import annotations

import logging
import re
from typing import Annotated, ClassVar, Literal

import httpx
from pydantic import BaseModel, Field
from sqlmodel import col, select

from common.database.postgres_database import SessionLocal
from common.database.postgres_models import (
    JobStatus,
    Minute,
    MinuteVersion,
    WorkflowAction,
    WorkflowActionStatus,
    WorkflowRun,
)
from common.llm.client import FastOrBestLLM, create_default_chatbot
from common.prompts import get_github_workflow_prompt
from common.settings import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pydantic schemas for the structured LLM response
# ---------------------------------------------------------------------------


class _CreateTicket(BaseModel):
    type: Literal["create_ticket"]
    title: str
    body: str
    labels: list[str] = []


class _UpdateTicket(BaseModel):
    type: Literal["update_ticket"]
    issue_number: int
    changes: dict


class _CloseTicket(BaseModel):
    type: Literal["close_ticket"]
    issue_number: int
    reason: str


# Discriminated union keyed on the "type" field
_ProposedAction = Annotated[
    _CreateTicket | _UpdateTicket | _CloseTicket,
    Field(discriminator="type"),
]


class _ProposedActions(BaseModel):
    """Wrapper so structured_chat has a single top-level Pydantic type to target."""

    items: list[_ProposedAction] = []


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _fetch_issues(repo: str, token: str) -> list[dict]:
    """Fetch all open issues from a GitHub repo, handling pagination."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    results: list[dict] = []
    url = f"https://api.github.com/repos/{repo}/issues"
    params: dict = {"state": "open", "per_page": 100}

    async with httpx.AsyncClient() as client:
        while url:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            page = response.json()
            results.extend(
                {
                    "number": issue["number"],
                    "title": issue["title"],
                    "body": issue.get("body") or "",
                    "labels": [label["name"] for label in issue.get("labels", [])],
                    "assignees": [a["login"] for a in issue.get("assignees", [])],
                    "url": issue["html_url"],
                }
                for issue in page
            )
            # Follow Link header for next page
            link_header = response.headers.get("Link", "")
            next_url = _parse_next_link(link_header)
            url = next_url  # type: ignore[assignment]
            params = {}  # next URL already includes query params

    return results


def _parse_next_link(link_header: str) -> str | None:
    """Extract the 'next' URL from a GitHub Link header."""
    match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
    return match.group(1) if match else None


def _strip_html(html: str) -> str:
    """Very lightweight HTML → plain-text (no external dependency required)."""
    # Remove script/style blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Replace block-level tags with newlines so paragraphs are readable
    text = re.sub(r"</(p|div|br|li|h[1-6]|tr)>", "\n", text, flags=re.IGNORECASE)
    # Strip all remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse excessive whitespace
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _human_readable(item: _CreateTicket | _UpdateTicket | _CloseTicket) -> str:
    """One-line description of a proposed action for display in the UI."""
    if isinstance(item, _CreateTicket):
        return f"Create ticket: {item.title}"
    if isinstance(item, _UpdateTicket):
        changed = ", ".join(item.changes.keys())
        return f"Update #{item.issue_number}: {changed}"
    if isinstance(item, _CloseTicket):
        return f"Close #{item.issue_number}: {item.reason}"
    return str(item)  # fallback — should never happen


def _load_latest_minutes_text(transcription_id) -> str:
    """Load the most recent COMPLETED MinuteVersion for the transcription.

    Uses a fresh synchronous session (prepare() runs in the worker process
    that already owns a separate sync session for the WorkflowRun itself).
    """
    with SessionLocal() as session:
        stmt = (
            select(MinuteVersion)
            .join(Minute, Minute.id == MinuteVersion.minute_id)
            .where(Minute.transcription_id == transcription_id)
            .where(MinuteVersion.status == JobStatus.COMPLETED)
            .order_by(col(MinuteVersion.created_datetime).desc())
            .limit(1)
        )
        minute_version = session.exec(stmt).first()

    if not minute_version:
        msg = (
            f"No completed MinuteVersion found for transcription {transcription_id}. "
            "Run the transcription through the minutes pipeline before starting a workflow."
        )
        raise ValueError(msg)
    return _strip_html(minute_version.html_content)


# ---------------------------------------------------------------------------
# Workflow class
# ---------------------------------------------------------------------------


class GithubProjectsWorkflow:
    name = "github_projects"
    display_name = "GitHub Projects"
    description = (
        "After an engineering meeting, review open GitHub Project tickets "
        "and propose updates, additions, or closures based on the minutes."
    )
    config_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "repo": {
                "type": "string",
                "title": "Repository",
                "description": "owner/repo — e.g. myorg/myrepo",
            },
            "project_number": {
                "type": "integer",
                "title": "Project number",
                "description": "The GitHub Project number (visible in the project URL)",
            },
        },
        "required": ["repo", "project_number"],
    }

    @classmethod
    async def prepare(cls, run: WorkflowRun) -> list[WorkflowAction]:
        """Fetch open issues, send to LLM with meeting minutes, return proposed actions."""
        settings = get_settings()

        # 1. Resolve credential
        token = settings.MINUTE_PAT_TOKEN
        if not token:
            msg = "MINUTE_PAT_TOKEN is not configured. Set it in your environment to use the GitHub Projects workflow."
            raise ValueError(msg)

        repo: str = run.config.get("repo", "")
        if not repo:
            msg = "Workflow config is missing required field 'repo'."
            raise ValueError(msg)

        # 2. Fetch open issues from GitHub
        issues = await _fetch_issues(repo, token)

        # 3. Load the latest completed minutes as plain text
        minutes_text = _load_latest_minutes_text(run.transcription_id)

        # 4. Call the LLM with structured output — no manual JSON parsing needed
        chatbot = create_default_chatbot(FastOrBestLLM.BEST)
        messages = get_github_workflow_prompt(issues, minutes_text)
        result = await chatbot.structured_chat(messages, _ProposedActions)

        # 5. Convert to WorkflowAction objects (unpersisted — caller sets workflow_run_id)
        return [
            WorkflowAction(
                position=i,
                action_type=item.type,
                description=_human_readable(item),
                payload=item.model_dump(),
                status=WorkflowActionStatus.PENDING,
            )
            for i, item in enumerate(result.items)
        ]

    @classmethod
    async def execute_action(cls, action: WorkflowAction) -> str | None:
        """Execute a single approved action (implemented in commit 06)."""
        msg = "execute_action will be implemented in the next commit."
        raise NotImplementedError(msg)
