"""Unit tests for GithubProjectsWorkflow.prepare().

All tests mock _fetch_issues (to avoid real HTTP) and create_default_chatbot
(to avoid real LLM calls). Uses unittest.mock.patch as context managers,
consistent with the existing codebase pattern.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.database.postgres_models import WorkflowActionStatus, WorkflowRun
from common.workflows.gh_projects_workflow import (
    _CloseTicket,
    _CreateTicket,
    _ProposedActions,
    _UpdateTicket,
)

FAKE_ISSUES = [
    {
        "number": 1,
        "title": "Fix login timeout",
        "body": "Users report...",
        "labels": [],
        "assignees": [],
        "url": "https://github.com/example/repo/issues/1",
    },
    {
        "number": 2,
        "title": "Add dark mode",
        "body": "Feature request",
        "labels": [],
        "assignees": [],
        "url": "https://github.com/example/repo/issues/2",
    },
]

# Pre-built _ProposedActions instances that the mock structured_chat returns
LLM_RESULT_CREATE = _ProposedActions(
    items=[_CreateTicket(type="create_ticket", title="New task", body="Details", labels=[])]
)
LLM_RESULT_CLOSE = _ProposedActions(
    items=[_CloseTicket(type="close_ticket", issue_number=1, reason="Resolved in meeting")]
)
LLM_RESULT_EMPTY = _ProposedActions(items=[])
LLM_RESULT_MULTIPLE = _ProposedActions(
    items=[
        _CreateTicket(type="create_ticket", title="New task", body="Details", labels=[]),
        _CloseTicket(type="close_ticket", issue_number=2, reason="Out of scope"),
        _UpdateTicket(type="update_ticket", issue_number=1, changes={"title": "Fixed"}),
    ]
)


def _make_run(config: dict | None = None) -> WorkflowRun:
    """Build a minimal mock WorkflowRun."""
    run = MagicMock(spec=WorkflowRun)
    run.id = uuid.uuid4()
    run.transcription_id = uuid.uuid4()
    run.config = config if config is not None else {"repo": "myorg/myrepo", "project_number": 42}
    return run


def _mock_chatbot(result: _ProposedActions) -> MagicMock:
    """Return a mock chatbot whose .structured_chat() returns the given _ProposedActions."""
    chatbot = MagicMock()
    chatbot.structured_chat = AsyncMock(return_value=result)
    return chatbot


def _patch_all(*, pat_token: str | None = "fake-token", llm_result: _ProposedActions = LLM_RESULT_EMPTY):  # noqa: S107
    """Return a tuple of patches for all external dependencies of prepare()."""
    return (
        patch(
            "common.workflows.gh_projects_workflow.get_settings",
            return_value=MagicMock(MINUTE_PAT_TOKEN=pat_token),
        ),
        patch(
            "common.workflows.gh_projects_workflow._fetch_issues",
            new=AsyncMock(return_value=FAKE_ISSUES),
        ),
        patch(
            "common.workflows.gh_projects_workflow._load_latest_minutes_text",
            return_value="We resolved the login timeout issue.",
        ),
        patch(
            "common.workflows.gh_projects_workflow.create_default_chatbot",
            return_value=_mock_chatbot(llm_result),
        ),
    )


class TestPrepare:
    @pytest.mark.asyncio
    async def test_returns_workflow_actions_for_create(self):
        """When the LLM proposes a create_ticket, prepare() returns one WorkflowAction."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        patches = _patch_all(llm_result=LLM_RESULT_CREATE)
        with patches[0], patches[1], patches[2], patches[3]:
            actions = await GithubProjectsWorkflow.prepare(run)

        assert len(actions) == 1
        assert actions[0].action_type == "create_ticket"
        assert actions[0].status == WorkflowActionStatus.PENDING
        assert actions[0].payload["title"] == "New task"

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
    async def test_raises_when_pat_token_missing(self):
        """When MINUTE_PAT_TOKEN is None, prepare() raises ValueError before calling the LLM."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        patches = _patch_all(pat_token=None)
        with patches[0], patches[1], patches[2], patches[3], pytest.raises(ValueError, match="MINUTE_PAT_TOKEN"):
            await GithubProjectsWorkflow.prepare(run)

    @pytest.mark.asyncio
    async def test_structured_chat_error_propagates(self):
        """When structured_chat raises (e.g. schema validation failure), the error propagates."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        bad_chatbot = MagicMock()
        bad_chatbot.structured_chat = AsyncMock(side_effect=ValueError("LLM schema mismatch"))

        patches = (
            patch(
                "common.workflows.gh_projects_workflow.get_settings",
                return_value=MagicMock(MINUTE_PAT_TOKEN="fake-token"),
            ),
            patch(
                "common.workflows.gh_projects_workflow._fetch_issues",
                new=AsyncMock(return_value=FAKE_ISSUES),
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

    @pytest.mark.asyncio
    async def test_action_descriptions_are_human_readable(self):
        """Each returned action has a non-empty, descriptive string in its description field."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        patches = _patch_all(llm_result=LLM_RESULT_MULTIPLE)
        with patches[0], patches[1], patches[2], patches[3]:
            actions = await GithubProjectsWorkflow.prepare(run)

        assert len(actions) == 3
        for action in actions:
            assert isinstance(action.description, str)
            assert len(action.description) > 0

        descriptions = [a.description for a in actions]
        assert any("Create ticket:" in d for d in descriptions)
        assert any("Close #2" in d for d in descriptions)
        assert any("Update #1" in d for d in descriptions)

    @pytest.mark.asyncio
    async def test_actions_have_sequential_positions(self):
        """Returned actions have position values 0, 1, 2... matching their list order."""
        from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

        run = _make_run()
        patches = _patch_all(llm_result=LLM_RESULT_MULTIPLE)
        with patches[0], patches[1], patches[2], patches[3]:
            actions = await GithubProjectsWorkflow.prepare(run)

        assert len(actions) == 3
        for i, action in enumerate(actions):
            assert action.position == i
