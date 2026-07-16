"""GitHub Projects agentic workflow."""

from __future__ import annotations

import logging
import re
from typing import ClassVar, Literal

import httpx
from pydantic import BaseModel
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

_GITHUB_API_BASE = "https://api.github.com"


class _ProjectContext(BaseModel):
    """Everything prepare() needs about the project."""

    # Numeric field ID used in REST PATCH body: {"fields": [{"id": status_field_id, "value": ...}]}
    status_field_id: int
    # Maps column name → option ID string, e.g. {"Todo": "opt_abc", "Blocked": "opt_def"}
    status_options: dict[str, str]
    items: list[_ProjectItem]


class _ProjectItem(BaseModel):
    # Numeric item ID used in REST PATCH: PATCH .../items/{item_id}
    item_id: int
    issue_number: int
    title: str
    body: str
    url: str
    current_status: str  # column name, e.g. "Todo", "In Progress", "Blocked"


class _CreateTicket(BaseModel):
    """Create a brand-new issue and add it to the project board."""

    type: Literal["create_ticket"]
    title: str
    body: str
    labels: list[str] = []
    initial_status: str = "Todo"  # must be one of the project's status option names


class _UpdateTicketBody(BaseModel):
    """Edit the title and/or body of an existing issue (REST PATCH /issues/{number})."""

    type: Literal["update_ticket_body"]
    issue_number: int
    title: str | None = None
    body: str | None = None


class _MoveTicket(BaseModel):
    """Move an existing project item to a different status column.

    execute_action() uses:
      PATCH /orgs/{org}/projectsV2/{project_number}/items/{item_id}
      body: {"fields": [{"id": status_field_id, "value": target_option_id}]}
    All IDs are resolved at prepare()-time so execute_action() is a dumb executor.
    """

    type: Literal["move_ticket"]
    issue_number: int  # for display only
    issue_title: str  # for display only
    target_status: str  # column name, e.g. "Blocked"
    # Resolved at prepare()-time:
    item_id: int  # numeric project item ID for the REST PATCH URL
    status_field_id: int  # numeric field ID for the REST PATCH body
    target_option_id: str  # single-select option ID string for the REST PATCH body


class _CloseTicket(BaseModel):
    """Close an existing issue via REST PATCH /repos/{owner}/{repo}/issues/{number}."""

    type: Literal["close_ticket"]
    issue_number: int
    reason: str


class _ProposedActions(BaseModel):
    """Wrapper so structured_chat has a single top-level Pydantic type to target."""

    create_actions: list[_CreateTicket]
    update_actions: list[_UpdateTicketBody]
    move_actions: list[_MoveTicket]
    close_actions: list[_CloseTicket]


def _gh_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _fetch_status_field(org: str, project_number: int, token: str) -> tuple[int, dict[str, str]]:
    """Return (numeric_field_id, {option_name: option_id}) for the project's Status field.

    Uses REST GET /orgs/{org}/projectsV2/{project_number}/fields which returns
    every field with its full options catalogue — including empty columns.
    """
    url = f"{_GITHUB_API_BASE}/orgs/{org}/projectsV2/{project_number}/fields"
    headers = _gh_headers(token)
    params: dict = {"per_page": 100}
    fields: list[dict] = []

    async with httpx.AsyncClient() as client:
        while url:
            logger.info("GET %s (params=%s)", url, params or "paginated")
            response = await client.get(url, headers=headers, params=params)
            logger.info("  → %s %s", response.status_code, response.reason_phrase)
            response.raise_for_status()
            body = response.json()
            page = body if isinstance(body, list) else [body]
            fields.extend(page)
            url = _parse_next_link(response.headers.get("Link", ""))
            params = {}

    for field in fields:
        if field.get("name") == "Status" and field.get("data_type") == "single_select":
            field_id = int(field["id"])
            options = {opt["name"]["raw"]: opt["id"] for opt in field.get("options", [])}
            logger.info(
                "Status field found: id=%d, options=[%s]",
                field_id,
                ", ".join(f'"{n}"' for n in options),
            )
            return field_id, options

    msg = (
        f"Project #{project_number} in org '{org}' has no 'Status' single-select field. "
        "Please add one before using this workflow."
    )
    raise ValueError(msg)


async def _fetch_project_items(org: str, project_number: int, token: str) -> _ProjectContext:
    """Fetch active project items and status field metadata via REST.

    Fetches up to 100 items in a single request and filters out closed issues
    and items in any 'Done'-style column. This is sufficient for any active
    engineering board — if you have more than 100 open items you have bigger
    problems than this workflow.
    """
    # 1. Get the Status field's numeric ID and full option catalogue
    logger.info("Fetching Status field for project #%d in org '%s'", project_number, org)
    status_field_id, status_options = await _fetch_status_field(org, project_number, token)

    done_statuses = {name for name in status_options if "done" in name.lower()}
    logger.info(
        "Available columns: %s | Skipping: %s",
        list(status_options),
        done_statuses or "(none)",
    )

    # 2. Single request — 100 items is enough for any active board
    url = f"{_GITHUB_API_BASE}/orgs/{org}/projectsV2/{project_number}/items"
    params = {"per_page": 100}
    logger.info("GET %s", url)

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=_gh_headers(token), params=params)
        logger.info("  → %s %s", response.status_code, response.reason_phrase)
        response.raise_for_status()
        body = response.json()

    raw_items = body if isinstance(body, list) else [body]
    active_items: list[_ProjectItem] = []

    for raw in raw_items:
        content = raw.get("content") or {}
        if not content or not content.get("number"):
            continue  # draft or no linked issue

        if content.get("state") == "closed":
            continue  # skip closed issues

        current_status = "Unknown"
        for field in raw.get("fields", []):
            if field.get("name") == "Status" and field.get("data_type") == "single_select":
                val = field.get("value") or {}
                name_val = val.get("name") or {}
                current_status = name_val.get("raw", "Unknown") if isinstance(name_val, dict) else str(name_val)
                break

        if current_status in done_statuses:
            continue  # skip completed items

        active_items.append(
            _ProjectItem(
                item_id=int(raw["id"]),
                issue_number=int(content["number"]),
                title=content.get("title", ""),
                body=content.get("body") or "",
                url=content.get("html_url", content.get("url", "")),
                current_status=current_status,
            )
        )

    logger.info(
        "Active items (%d): %s",
        len(active_items),
        ", ".join(f'#{i.issue_number} "{i.title}" [{i.current_status}]' for i in active_items) or "(none)",
    )
    return _ProjectContext(
        status_field_id=status_field_id,
        status_options=status_options,
        items=active_items,
    )


def _parse_next_link(link_header: str) -> str | None:
    """Extract the 'next' URL from a GitHub Link header."""
    match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
    return match.group(1) if match else None


def _strip_html(html: str) -> str:
    """Very lightweight HTML → plain-text (no external dependency required)."""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"</(p|div|br|li|h[1-6]|tr)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _human_readable(item: _CreateTicket | _UpdateTicketBody | _MoveTicket | _CloseTicket) -> str:
    """One-line description of a proposed action for display in the UI."""
    if isinstance(item, _CreateTicket):
        return f"Create ticket: {item.title} (→ {item.initial_status})"
    if isinstance(item, _UpdateTicketBody):
        changed = ", ".join(k for k in ("title", "body") if getattr(item, k) is not None)
        return f"Update #{item.issue_number}: edit {changed}"
    if isinstance(item, _MoveTicket):
        return f"Move #{item.issue_number} '{item.issue_title}': → {item.target_status}"
    if isinstance(item, _CloseTicket):
        return f"Close #{item.issue_number}: {item.reason}"
    return str(item)


def _load_latest_minutes_text(transcription_id) -> str:
    """Load the most recent COMPLETED MinuteVersion for the transcription."""
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
            "org": {
                "type": "string",
                "title": "Organisation",
                "description": "GitHub organisation login — e.g. myorg",
            },
            "repo": {
                "type": "string",
                "title": "Repository",
                "description": "owner/repo — e.g. myorg/myrepo (used when creating new issues)",
            },
            "project_number": {
                "type": "integer",
                "title": "Project number",
                "description": "The GitHub Project number (visible in the project URL)",
            },
        },
        "required": ["org", "repo", "project_number"],
    }

    @classmethod
    async def prepare(cls, run: WorkflowRun) -> list[WorkflowAction]:
        """Fetch project items, call the LLM, return proposed actions."""
        settings = get_settings()

        token = settings.MINUTE_PAT_TOKEN
        if not token:
            msg = (
                "MINUTE_PAT_TOKEN is not configured. " "Set it in your environment to use the GitHub Projects workflow."
            )
            raise ValueError(msg)

        org: str = run.config.get("org", "")
        if not org:
            msg = "Workflow config is missing required field 'org'."
            raise ValueError(msg)

        project_number: int = run.config.get("project_number")
        if not project_number:
            msg = "Workflow config is missing required field 'project_number'."
            raise ValueError(msg)

        # 1. Fetch project items + status field metadata
        ctx = await _fetch_project_items(org, project_number, token)

        # 2. Load the latest completed minutes as plain text
        minutes_text = _load_latest_minutes_text(run.transcription_id)

        # 3. Call the LLM with structured output
        logger.info("Calling LLM (%s) to propose actions", FastOrBestLLM.BEST.name)
        chatbot = create_default_chatbot(FastOrBestLLM.BEST)
        messages = get_github_workflow_prompt(ctx, minutes_text)
        result = await chatbot.structured_chat(messages, _ProposedActions)

        total_proposed = (
            len(result.create_actions)
            + len(result.update_actions)
            + len(result.move_actions)
            + len(result.close_actions)
        )
        logger.info(
            "LLM proposed %d action(s): %d create, %d update, %d move, %d close",
            total_proposed,
            len(result.create_actions),
            len(result.update_actions),
            len(result.move_actions),
            len(result.close_actions),
        )

        # 4. Flatten all action lists; resolve move_ticket IDs from project context
        all_proposed: list[_CreateTicket | _UpdateTicketBody | _MoveTicket | _CloseTicket] = [
            *result.create_actions,
            *result.update_actions,
            *result.move_actions,
            *result.close_actions,
        ]
        resolved: list[_CreateTicket | _UpdateTicketBody | _MoveTicket | _CloseTicket] = []
        for item in all_proposed:
            if isinstance(item, _MoveTicket):
                option_id = ctx.status_options.get(item.target_status)
                if not option_id:
                    available = ", ".join(f'"{s}"' for s in ctx.status_options)
                    logger.warning(
                        "LLM proposed move to unknown status %r (available: %s) — skipping",
                        item.target_status,
                        available,
                    )
                    continue
                resolved.append(
                    _MoveTicket(
                        type="move_ticket",
                        issue_number=item.issue_number,
                        issue_title=item.issue_title,
                        target_status=item.target_status,
                        item_id=item.item_id,
                        status_field_id=ctx.status_field_id,
                        target_option_id=option_id,
                    )
                )
            else:
                resolved.append(item)

        # 5. Convert to WorkflowAction objects (unpersisted — caller sets workflow_run_id)
        actions = [
            WorkflowAction(
                position=i,
                action_type=action.type,
                description=_human_readable(action),
                payload=action.model_dump(),
                status=WorkflowActionStatus.PENDING,
            )
            for i, action in enumerate(resolved)
        ]
        logger.info(
            "prepare() returning %d action(s): %s",
            len(actions),
            ", ".join(a.description for a in actions) or "(none)",
        )
        return actions

    @classmethod
    async def execute_action(cls, action: WorkflowAction) -> str | None:
        """Execute a single approved action (implemented in commit 06)."""
        msg = "execute_action will be implemented in the next commit."
        raise NotImplementedError(msg)
