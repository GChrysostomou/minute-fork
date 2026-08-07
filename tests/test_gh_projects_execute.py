"""Unit tests for GithubProjectsWorkflow.execute_action().

All tests mock httpx.AsyncClient to avoid real network calls.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from common.workflows.gh_projects_workflow import GithubProjectsWorkflow

_FAKE_CONFIG = {"org": "myorg", "repo": "myorg/myrepo", "project_number": 1}


def _make_action(action_type: str, payload: dict, config: dict | None = None) -> MagicMock:
    """Build a minimal WorkflowAction-like mock."""
    action = MagicMock()
    action.action_type = action_type
    action.payload = payload
    action.workflow_run.config = config or _FAKE_CONFIG
    return action


def _mock_response(status_code: int = 200, json_body: dict | None = None) -> MagicMock:
    """Return a mock httpx response."""
    response = MagicMock()
    response.status_code = status_code
    response.reason_phrase = "OK" if status_code == 200 else "Error"
    response.json.return_value = json_body or {}
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=f"{status_code}",
            request=MagicMock(),
            response=response,
        )
    else:
        response.raise_for_status.return_value = None
    return response


def _patch_github_token(token: str | None = "fake-token"):  # noqa: S107
    return patch(
        "common.workflows.gh_projects_workflow.get_user_github_token",
        new=AsyncMock(return_value=token),
    )


class TestExecuteActionCreateTicket:
    @pytest.mark.asyncio
    async def test_creates_issue_and_adds_to_board(self):
        """create_ticket POSTs the issue then POSTs to the project board."""
        action = _make_action(
            "create_ticket",
            {"title": "New task", "body": "Details", "labels": [], "initial_status": ""},
        )
        issue_response = _mock_response(201, {"html_url": "https://github.com/myorg/myrepo/issues/99", "id": 999})
        add_response = _mock_response(201, {"value": {"id": 42}})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[issue_response, add_response])

        with (
            _patch_github_token(),
            patch("common.workflows.gh_projects_workflow.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await GithubProjectsWorkflow.execute_action(action)

        assert result == "https://github.com/myorg/myrepo/issues/99"
        assert mock_client.post.call_count == 2
        # First call: create issue
        issue_call = mock_client.post.call_args_list[0]
        assert "issues" in issue_call.args[0]
        assert issue_call.kwargs["json"]["title"] == "New task"
        # Second call: add to board
        board_call = mock_client.post.call_args_list[1]
        assert "projectsV2" in board_call.args[0]
        assert board_call.kwargs["json"]["id"] == 999

    @pytest.mark.asyncio
    async def test_sets_initial_status_when_provided(self):
        """When initial_status is set, a PATCH is issued to set the column."""
        action = _make_action(
            "create_ticket",
            {"title": "Task", "body": "", "labels": [], "initial_status": "In Progress"},
        )
        issue_response = _mock_response(201, {"html_url": "https://github.com/myorg/myrepo/issues/99", "id": 999})
        add_response = _mock_response(201, {"value": {"id": 42}})
        status_response = _mock_response(200, {})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=[issue_response, add_response])
        mock_client.patch = AsyncMock(return_value=status_response)

        fake_field = (5, {"In Progress": "opt_ip", "Todo": "opt_todo"})
        with (
            _patch_github_token(),
            patch("common.workflows.gh_projects_workflow.httpx.AsyncClient", return_value=mock_client),
            patch("common.workflows.gh_projects_workflow._fetch_status_field", new=AsyncMock(return_value=fake_field)),
        ):
            result = await GithubProjectsWorkflow.execute_action(action)

        assert result == "https://github.com/myorg/myrepo/issues/99"
        mock_client.patch.assert_called_once()
        patch_body = mock_client.patch.call_args.kwargs["json"]
        assert patch_body["fields"][0]["value"] == "opt_ip"


class TestExecuteActionUpdateTicketBody:
    @pytest.mark.asyncio
    async def test_patches_issue_body(self):
        """update_ticket_body PATCHes the issue and returns its html_url."""
        action = _make_action(
            "update_ticket_body",
            {"issue_number": 7, "body": "Updated body.", "title": None},
        )
        mock_response = _mock_response(200, {"html_url": "https://github.com/myorg/myrepo/issues/7"})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.patch = AsyncMock(return_value=mock_response)

        with (
            _patch_github_token(),
            patch("common.workflows.gh_projects_workflow.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await GithubProjectsWorkflow.execute_action(action)

        assert result == "https://github.com/myorg/myrepo/issues/7"
        patch_body = mock_client.patch.call_args.kwargs["json"]
        assert patch_body == {"body": "Updated body."}
        assert "title" not in patch_body  # None values excluded

    @pytest.mark.asyncio
    async def test_patches_title_and_body(self):
        """Both title and body are included when both are provided."""
        action = _make_action(
            "update_ticket_body",
            {"issue_number": 7, "title": "New title", "body": "New body"},
        )
        mock_response = _mock_response(200, {"html_url": "https://github.com/myorg/myrepo/issues/7"})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.patch = AsyncMock(return_value=mock_response)

        with (
            _patch_github_token(),
            patch("common.workflows.gh_projects_workflow.httpx.AsyncClient", return_value=mock_client),
        ):
            await GithubProjectsWorkflow.execute_action(action)

        patch_body = mock_client.patch.call_args.kwargs["json"]
        assert patch_body == {"title": "New title", "body": "New body"}


class TestExecuteActionMoveTicket:
    @pytest.mark.asyncio
    async def test_patches_project_item_status(self):
        """move_ticket PATCHes the project item's Status field and returns None."""
        action = _make_action(
            "move_ticket",
            {
                "issue_number": 2,
                "issue_title": "Migration",
                "target_status": "Blocked",
                "item_id": 102,
                "status_field_id": 3,
                "target_option_id": "opt_blocked",
            },
        )
        mock_response = _mock_response(200, {"id": 102})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.patch = AsyncMock(return_value=mock_response)

        with (
            _patch_github_token(),
            patch("common.workflows.gh_projects_workflow.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await GithubProjectsWorkflow.execute_action(action)

        assert result is None
        patch_call = mock_client.patch.call_args
        assert "projectsV2" in patch_call.args[0]
        assert "102" in patch_call.args[0]
        body = patch_call.kwargs["json"]
        assert body["fields"] == [{"id": 3, "value": "opt_blocked"}]


class TestExecuteActionCloseTicket:
    @pytest.mark.asyncio
    async def test_closes_issue_with_state_completed(self):
        """close_ticket PATCHes state=closed and state_reason=completed."""
        action = _make_action(
            "close_ticket",
            {"issue_number": 3, "reason": "Resolved in meeting"},
        )
        mock_response = _mock_response(200, {"html_url": "https://github.com/myorg/myrepo/issues/3"})

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.patch = AsyncMock(return_value=mock_response)

        with (
            _patch_github_token(),
            patch("common.workflows.gh_projects_workflow.httpx.AsyncClient", return_value=mock_client),
        ):
            result = await GithubProjectsWorkflow.execute_action(action)

        assert result == "https://github.com/myorg/myrepo/issues/3"
        patch_body = mock_client.patch.call_args.kwargs["json"]
        assert patch_body["state"] == "closed"
        assert patch_body["state_reason"] == "completed"


class TestExecuteActionEdgeCases:
    @pytest.mark.asyncio
    async def test_raises_when_github_token_missing(self):
        action = _make_action("close_ticket", {"issue_number": 1, "reason": "done"})
        with _patch_github_token(token=None), pytest.raises(ValueError, match="GitHub session"):
            await GithubProjectsWorkflow.execute_action(action)

    @pytest.mark.asyncio
    async def test_raises_on_unknown_action_type(self):
        action = _make_action("delete_repo", {})
        with _patch_github_token(), pytest.raises(ValueError, match="Unknown action_type"):
            await GithubProjectsWorkflow.execute_action(action)

    @pytest.mark.asyncio
    async def test_http_error_propagates(self):
        """A 422 from GitHub propagates as HTTPStatusError (caller marks action FAILED)."""
        action = _make_action(
            "close_ticket",
            {"issue_number": 1, "reason": "done"},
        )
        mock_response = _mock_response(422)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.patch = AsyncMock(return_value=mock_response)

        with (
            _patch_github_token(),
            patch("common.workflows.gh_projects_workflow.httpx.AsyncClient", return_value=mock_client),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await GithubProjectsWorkflow.execute_action(action)

    @pytest.mark.asyncio
    async def test_403_raises_friendly_org_access_error(self):
        """A 403 (org hasn't approved the OAuth App) is rewritten into an actionable ValueError."""
        action = _make_action(
            "close_ticket",
            {"issue_number": 1, "reason": "done"},
            config={"org": "myorg", "repo": "myorg/myrepo", "project_number": 1},
        )
        mock_response = _mock_response(403)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.patch = AsyncMock(return_value=mock_response)

        with (
            _patch_github_token(),
            patch("common.workflows.gh_projects_workflow.httpx.AsyncClient", return_value=mock_client),
            pytest.raises(ValueError, match="myorg"),
        ):
            await GithubProjectsWorkflow.execute_action(action)
