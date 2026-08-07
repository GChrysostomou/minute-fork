"""Unit tests for GithubProjectsWorkflow.prepare().

All tests mock _fetch_project_items (to avoid real HTTP) and
create_default_chatbot (to avoid real LLM calls). Uses unittest.mock.patch
as context managers, consistent with the existing codebase pattern.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from common.database.postgres_models import WorkflowRun
from common.workflows.gh_projects_workflow import (
    _CloseTicket,
    _CreateTicket,
    _MoveTicket,
    _ProjectContext,
    _ProjectItem,
    _ProposedActions,
    _UpdateTicketBody,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

FAKE_CONTEXT = _ProjectContext(
    status_field_id=3,
    status_options={
        "Todo": "opt_todo",
        "In Progress": "opt_inprogress",
        "Blocked": "opt_blocked",
        "Done": "opt_done",
    },
    repo="myorg/myrepo",
    items=[
        _ProjectItem(
            item_id=101,
            issue_number=1,
            title="Refactor",
            body="Refactor the core module.",
            url="https://github.com/myorg/myrepo/issues/1",
            repo="myorg/myrepo",
            current_status="Todo",
        ),
        _ProjectItem(
            item_id=102,
            issue_number=2,
            title="Migration to python3.14",
            body="Migrate all services to Python 3.14.",
            url="https://github.com/myorg/myrepo/issues/2",
            repo="myorg/myrepo",
            current_status="In Progress",
        ),
        _ProjectItem(
            item_id=103,
            issue_number=3,
            title="Claude code get access",
            body="Get org-wide access to Claude Code.",
            url="https://github.com/myorg/myrepo/issues/3",
            repo="myorg/myrepo",
            current_status="In Progress",
        ),
    ],
)

LLM_RESULT_MOVE = _ProposedActions(
    create_actions=[],
    update_actions=[],
    move_actions=[
        _MoveTicket(
            type="move_ticket",
            issue_number=2,
            issue_title="Migration to python3.14",
            target_status="Blocked",
            item_id=102,
            status_field_id=0,  # back-filled by prepare()
            target_option_id="",  # back-filled by prepare()
        ),
    ],
    close_actions=[],
)

LLM_RESULT_UPDATE_BODY = _ProposedActions(
    create_actions=[],
    update_actions=[
        _UpdateTicketBody(
            type="update_ticket_body",
            issue_number=1,
            body="Deprioritised until Python 3.14 migration completes.",
        ),
    ],
    move_actions=[],
    close_actions=[],
)

LLM_RESULT_CREATE = _ProposedActions(
    create_actions=[
        _CreateTicket(
            type="create_ticket",
            title="Add rate-limiter burst config",
            body="Per-user burst limit needed.",
            labels=["enhancement"],
            initial_status="Todo",
        ),
    ],
    update_actions=[],
    move_actions=[],
    close_actions=[],
)

LLM_RESULT_CLOSE = _ProposedActions(
    create_actions=[],
    update_actions=[],
    move_actions=[],
    close_actions=[
        _CloseTicket(
            type="close_ticket",
            issue_number=3,
            reason="Licence confirmed and activated.",
        ),
    ],
)

LLM_RESULT_EMPTY = _ProposedActions(
    create_actions=[],
    update_actions=[],
    move_actions=[],
    close_actions=[],
)

LLM_RESULT_MULTIPLE = _ProposedActions(
    create_actions=[
        _CreateTicket(
            type="create_ticket",
            title="New task",
            body="Details",
            labels=[],
            initial_status="Todo",
        ),
    ],
    update_actions=[
        _UpdateTicketBody(
            type="update_ticket_body",
            issue_number=1,
            body="Deprioritised until migration completes.",
        ),
    ],
    move_actions=[
        _MoveTicket(
            type="move_ticket",
            issue_number=2,
            issue_title="Migration to python3.14",
            target_status="Blocked",
            item_id=102,
            status_field_id=0,
            target_option_id="",
        ),
    ],
    close_actions=[],
)

LLM_RESULT_UNKNOWN_STATUS = _ProposedActions(
    create_actions=[],
    update_actions=[],
    move_actions=[
        _MoveTicket(
            type="move_ticket",
            issue_number=2,
            issue_title="Migration to python3.14",
            target_status="Nonexistent Column",
            item_id=102,
            status_field_id=0,
            target_option_id="",
        ),
    ],
    close_actions=[],
)


def _make_run(config: dict | None = None) -> WorkflowRun:
    """Build a minimal mock WorkflowRun."""
    run = MagicMock(spec=WorkflowRun)
    run.id = uuid.uuid4()
    run.user_id = uuid.uuid4()
    run.transcription_id = uuid.uuid4()
    run.config = (
        config
        if config is not None
        else {
            "org": "myorg",
            "repo": "myorg/myrepo",
            "project_number": 1,
        }
    )
    return run


def _mock_chatbot(result: _ProposedActions) -> MagicMock:
    """Return a mock chatbot whose .structured_chat() returns the given _ProposedActions."""
    chatbot = MagicMock()
    chatbot.structured_chat = AsyncMock(return_value=result)
    return chatbot


def _patch_all(
    *,
    fake_token: str | None = "fake-token",  # noqa: S107
    llm_result: _ProposedActions = LLM_RESULT_EMPTY,
    ctx: _ProjectContext = FAKE_CONTEXT,
):
    """Return a tuple of patches for all external dependencies of prepare()."""
    return (
        patch(
            "common.workflows.gh_projects_workflow.get_user_github_token",
            new=AsyncMock(return_value=fake_token),
        ),
        patch(
            "common.workflows.gh_projects_workflow._fetch_project_items",
            new=AsyncMock(return_value=ctx),
        ),
        patch(
            "common.workflows.gh_projects_workflow._load_latest_minutes_text",
            return_value="We discussed the board.",
        ),
        patch(
            "common.workflows.gh_projects_workflow.create_default_chatbot",
            return_value=_mock_chatbot(llm_result),
        ),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPrepare:
    @pytest.mark.asyncio
    async def test_repo_backfilled_from_context_into_payloads(self):
        """prepare() back-fills repo from project context into create/update/close payloads."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        patches = _patch_all(llm_result=LLM_RESULT_MULTIPLE)
        with patches[0], patches[1], patches[2], patches[3]:
            actions = await GithubProjectsWorkflow.prepare(run)

        for action in actions:
            if action.action_type in ("create_ticket", "update_ticket_body", "close_ticket"):
                assert action.payload.get("repo") == "myorg/myrepo", f"{action.action_type} payload missing repo"

        """prepare() resolves target_option_id and status_field_id from project context.
        The LLM only provides issue_number, item_id, and target_status."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        patches = _patch_all(llm_result=LLM_RESULT_MOVE)
        with patches[0], patches[1], patches[2], patches[3]:
            actions = await GithubProjectsWorkflow.prepare(run)

        assert len(actions) == 1
        assert actions[0].action_type == "move_ticket"
        payload = actions[0].payload
        assert payload["target_option_id"] == "opt_blocked"
        assert payload["status_field_id"] == FAKE_CONTEXT.status_field_id

    @pytest.mark.asyncio
    async def test_move_ticket_unknown_status_is_skipped(self):
        """If the LLM names a non-existent status column, the action is silently dropped."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        patches = _patch_all(llm_result=LLM_RESULT_UNKNOWN_STATUS)
        with patches[0], patches[1], patches[2], patches[3]:
            actions = await GithubProjectsWorkflow.prepare(run)

        assert actions == []

    @pytest.mark.asyncio
    async def test_returns_update_ticket_body_action(self):
        """update_ticket_body actions pass through unchanged with correct type."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        patches = _patch_all(llm_result=LLM_RESULT_UPDATE_BODY)
        with patches[0], patches[1], patches[2], patches[3]:
            actions = await GithubProjectsWorkflow.prepare(run)

        assert len(actions) == 1
        assert actions[0].action_type == "update_ticket_body"
        assert actions[0].payload["issue_number"] == 1

    @pytest.mark.asyncio
    async def test_returns_create_ticket_action(self):
        """create_ticket actions pass through with initial_status preserved."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        patches = _patch_all(llm_result=LLM_RESULT_CREATE)
        with patches[0], patches[1], patches[2], patches[3]:
            actions = await GithubProjectsWorkflow.prepare(run)

        assert len(actions) == 1
        assert actions[0].action_type == "create_ticket"
        assert actions[0].payload["initial_status"] == "Todo"

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_llm_says_no_changes(self):
        """When the LLM returns no items, prepare() returns an empty list."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        patches = _patch_all(llm_result=LLM_RESULT_EMPTY)
        with patches[0], patches[1], patches[2], patches[3]:
            actions = await GithubProjectsWorkflow.prepare(run)

        assert actions == []

    @pytest.mark.asyncio
    async def test_raises_when_github_token_missing(self):
        """When the user has no cached GitHub token, prepare() raises ValueError immediately."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        patches = _patch_all(fake_token=None)
        with patches[0], patches[1], patches[2], patches[3], pytest.raises(ValueError, match="GitHub session"):
            await GithubProjectsWorkflow.prepare(run)

    @pytest.mark.asyncio
    async def test_403_raises_friendly_org_access_error(self):
        """A 403 from GitHub (org hasn't approved the OAuth App) is rewritten into an actionable ValueError."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        forbidden_response = MagicMock(status_code=403)
        forbidden_error = httpx.HTTPStatusError("403", request=MagicMock(), response=forbidden_response)
        patches = (
            patch(
                "common.workflows.gh_projects_workflow.get_user_github_token",
                new=AsyncMock(return_value="fake-token"),
            ),
            patch(
                "common.workflows.gh_projects_workflow._fetch_project_items",
                new=AsyncMock(side_effect=forbidden_error),
            ),
        )
        with patches[0], patches[1], pytest.raises(ValueError, match="myorg"):
            await GithubProjectsWorkflow.prepare(run)

    @pytest.mark.asyncio
    async def test_raises_when_config_missing_org(self):
        """Missing 'org' in run.config raises ValueError."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run(config={"repo": "myorg/myrepo", "project_number": 1})
        patches = _patch_all()
        with patches[0], patches[1], patches[2], patches[3], pytest.raises(ValueError, match="'org'"):
            await GithubProjectsWorkflow.prepare(run)

    @pytest.mark.asyncio
    async def test_actions_have_sequential_positions(self):
        """Returned actions have position values 0, 1, 2 matching their list order."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        patches = _patch_all(llm_result=LLM_RESULT_MULTIPLE)
        with patches[0], patches[1], patches[2], patches[3]:
            actions = await GithubProjectsWorkflow.prepare(run)

        assert len(actions) == 3
        for i, action in enumerate(actions):
            assert action.position == i

    @pytest.mark.asyncio
    async def test_action_descriptions_are_human_readable(self):
        """Each action has a non-empty description string appropriate to its type."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        patches = _patch_all(llm_result=LLM_RESULT_MULTIPLE)
        with patches[0], patches[1], patches[2], patches[3]:
            actions = await GithubProjectsWorkflow.prepare(run)

        descriptions = [a.description for a in actions]
        assert all(isinstance(d, str) and len(d) > 0 for d in descriptions)
        assert any("Move #2" in d for d in descriptions)
        assert any("Update #1" in d for d in descriptions)
        assert any("Create ticket:" in d for d in descriptions)

    @pytest.mark.asyncio
    async def test_structured_chat_error_propagates(self):
        """An error raised by structured_chat propagates out of prepare()."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        bad_chatbot = MagicMock()
        bad_chatbot.structured_chat = AsyncMock(side_effect=ValueError("LLM schema mismatch"))

        patches = (
            patch(
                "common.workflows.gh_projects_workflow.get_user_github_token",
                new=AsyncMock(return_value="fake-token"),
            ),
            patch(
                "common.workflows.gh_projects_workflow._fetch_project_items",
                new=AsyncMock(return_value=FAKE_CONTEXT),
            ),
            patch(
                "common.workflows.gh_projects_workflow._load_latest_minutes_text",
                return_value="Some minutes.",
            ),
            patch(
                "common.workflows.gh_projects_workflow.create_default_chatbot",
                return_value=bad_chatbot,
            ),
        )
        with patches[0], patches[1], patches[2], patches[3], pytest.raises(ValueError, match="LLM schema mismatch"):
            await GithubProjectsWorkflow.prepare(run)
