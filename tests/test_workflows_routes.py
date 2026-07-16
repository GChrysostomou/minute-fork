"""HTTP tests for /workflows routes.

Uses FastAPI dependency overrides to substitute a mock async session and mock
queue service so no real database or SQS instance is required.
"""

import uuid
from datetime import UTC, datetime
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from backend.api.dependencies.get_session import get_session
from backend.api.routes.workflows import llm_queue_service
from backend.main import app
from common.database.postgres_models import WorkflowActionStatus, WorkflowRun, WorkflowStatus
from common.workflows.workflow_manager import WorkflowManager

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _make_run(
    run_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    status: WorkflowStatus = WorkflowStatus.AWAITING_START,
    actions: list | None = None,
) -> WorkflowRun:
    run = MagicMock(spec=WorkflowRun)
    run.id = run_id or uuid.uuid4()
    run.transcription_id = uuid.uuid4()
    run.user_id = user_id or uuid.uuid4()
    run.workflow_name = "fake"
    run.config = {}
    run.status = status
    run.error = None
    run.created_datetime = _NOW
    run.updated_datetime = _NOW
    run.actions = actions or []
    return run


def _mock_session(get_result=None, exec_result=None):
    """Return an async mock session suitable for FastAPI dependency override."""
    session = AsyncMock()
    session.get = AsyncMock(return_value=get_result)
    mock_exec = AsyncMock()
    mock_exec.first = MagicMock(return_value=exec_result)
    session.exec = AsyncMock(return_value=mock_exec)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.delete = AsyncMock()
    return session


def _session_override(session):
    """Return a FastAPI-compatible dependency override for the DB session."""

    async def _override():
        yield session

    return _override


def _user_override(user):
    """Return a FastAPI-compatible dependency override for the current user."""

    def _override():
        return user

    return _override


@pytest.fixture(autouse=True)
def reset_workflow_registry():
    WorkflowManager.reset()
    yield
    WorkflowManager.reset()


@pytest.fixture
def mock_queue():
    with patch.object(llm_queue_service, "publish_message") as mock_pub:
        yield mock_pub


@pytest.mark.asyncio(loop_scope="session")
class TestListWorkflows:
    async def test_returns_200_and_empty_list_when_no_workflows_registered(self, mock_queue):  # noqa: ARG002
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/workflows")
        assert response.status_code == 200
        assert response.json() == []

    async def test_returns_registered_workflow_metadata(self, mock_queue):  # noqa: ARG002
        class FakeWorkflow:
            name = "fake"
            display_name = "Fake"
            description = "A fake."
            config_schema: ClassVar[dict] = {"type": "object"}

            @classmethod
            async def prepare(cls, _run):
                return []

            @classmethod
            async def execute_action(cls, _action):
                return None

        WorkflowManager.register_workflow(FakeWorkflow)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/workflows")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "fake"
        assert data[0]["display_name"] == "Fake"


# ---------------------------------------------------------------------------
# POST /workflows
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
class TestCreateWorkflowRun:
    async def test_unknown_workflow_returns_400(self, mock_queue):  # noqa: ARG002
        session = _mock_session()
        app.dependency_overrides[get_session] = _session_override(session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/workflows",
                    json={
                        "transcription_id": str(uuid.uuid4()),
                        "workflow_name": "nonexistent",
                        "config": {},
                    },
                )
        finally:
            app.dependency_overrides.pop(get_session, None)

        assert response.status_code == 400

    async def test_unknown_transcription_returns_404(self, mock_queue):  # noqa: ARG002
        class FakeWorkflow:
            name = "fake"
            display_name = "Fake"
            description = "desc"
            config_schema: ClassVar[dict] = {}

            @classmethod
            async def prepare(cls, _run):
                return []

            @classmethod
            async def execute_action(cls, _action):
                return None

        WorkflowManager.register_workflow(FakeWorkflow)

        # session.get returns None → transcription not found
        session = _mock_session(get_result=None)
        app.dependency_overrides[get_session] = _session_override(session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/workflows",
                    json={
                        "transcription_id": str(uuid.uuid4()),
                        "workflow_name": "fake",
                        "config": {},
                    },
                )
        finally:
            app.dependency_overrides.pop(get_session, None)

        assert response.status_code == 404

    async def test_creates_run_and_enqueues_prepare(self, mock_queue):
        from backend.api.dependencies.get_current_user import get_current_user
        from common.database.postgres_models import Transcription, User

        class FakeWorkflow:
            name = "fake"
            display_name = "Fake"
            description = "desc"
            config_schema: ClassVar[dict] = {}

            @classmethod
            async def prepare(cls, _run):
                return []

            @classmethod
            async def execute_action(cls, _action):
                return None

        WorkflowManager.register_workflow(FakeWorkflow)

        user_id = uuid.uuid4()
        trans_id = uuid.uuid4()

        mock_user = MagicMock(spec=User)
        mock_user.id = user_id

        mock_transcription = MagicMock(spec=Transcription)
        mock_transcription.user_id = user_id

        run_id = uuid.uuid4()

        session = _mock_session(get_result=mock_transcription)

        async def fake_refresh(obj):
            obj.id = run_id
            obj.transcription_id = trans_id
            obj.user_id = user_id
            obj.workflow_name = "fake"
            obj.config = {"key": "val"}
            obj.status = WorkflowStatus.AWAITING_START
            obj.error = None
            obj.created_datetime = _NOW
            obj.updated_datetime = _NOW
            obj.actions = []

        session.refresh = fake_refresh

        app.dependency_overrides[get_session] = _session_override(session)
        app.dependency_overrides[get_current_user] = _user_override(mock_user)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/workflows",
                    json={
                        "transcription_id": str(trans_id),
                        "workflow_name": "fake",
                        "config": {"key": "val"},
                    },
                )
        finally:
            app.dependency_overrides.pop(get_session, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 201
        mock_queue.assert_called_once()
        payload = mock_queue.call_args[0][0]
        from common.types import TaskType

        assert payload.type == TaskType.WORKFLOW_PREPARE


@pytest.mark.asyncio(loop_scope="session")
class TestGetWorkflowRun:
    async def test_returns_404_when_not_found(self, mock_queue):  # noqa: ARG002
        session = _mock_session(exec_result=None)
        app.dependency_overrides[get_session] = _session_override(session)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(f"/workflows/{uuid.uuid4()}")
        finally:
            app.dependency_overrides.pop(get_session, None)

        assert response.status_code == 404

    async def test_returns_404_for_other_users_run(self, mock_queue):  # noqa: ARG002
        from backend.api.dependencies.get_current_user import get_current_user
        from common.database.postgres_models import User

        requesting_user = MagicMock(spec=User)
        requesting_user.id = uuid.uuid4()

        other_user_id = uuid.uuid4()
        run = _make_run(user_id=other_user_id)
        session = _mock_session(exec_result=run)
        app.dependency_overrides[get_session] = _session_override(session)
        app.dependency_overrides[get_current_user] = _user_override(requesting_user)
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(f"/workflows/{run.id}")
        finally:
            app.dependency_overrides.pop(get_session, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 404

    async def test_returns_200_with_run_data(self, mock_queue):  # noqa: ARG002
        # The local env user is auto-created as test@test.co.uk.
        # We can't know its UUID without DB, so we patch get_current_user instead.
        user_id = uuid.uuid4()
        run = _make_run(user_id=user_id, status=WorkflowStatus.AWAITING_CONFIRMATION)

        session = _mock_session(exec_result=run)

        from backend.api.dependencies.get_current_user import get_current_user
        from common.database.postgres_models import User

        mock_user = MagicMock(spec=User)
        mock_user.id = user_id

        app.dependency_overrides[get_session] = _session_override(session)
        app.dependency_overrides[get_current_user] = lambda: mock_user
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(f"/workflows/{run.id}")
        finally:
            app.dependency_overrides.pop(get_session, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(run.id)
        assert data["status"] == WorkflowStatus.AWAITING_CONFIRMATION
        assert data["actions"] == []


@pytest.mark.asyncio(loop_scope="session")
class TestExecuteWorkflowRun:
    async def test_wrong_status_returns_409(self, mock_queue):  # noqa: ARG002
        user_id = uuid.uuid4()
        run = _make_run(user_id=user_id, status=WorkflowStatus.AWAITING_START)

        session = _mock_session(exec_result=run)

        from backend.api.dependencies.get_current_user import get_current_user
        from common.database.postgres_models import User

        mock_user = MagicMock(spec=User)
        mock_user.id = user_id

        app.dependency_overrides[get_session] = _session_override(session)
        app.dependency_overrides[get_current_user] = lambda: mock_user
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    f"/workflows/{run.id}/execute",
                    json={"decisions": []},
                )
        finally:
            app.dependency_overrides.pop(get_session, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 409

    async def test_decisions_are_applied_and_execute_enqueued(self, mock_queue):
        user_id = uuid.uuid4()
        action_id = uuid.uuid4()

        mock_action = MagicMock()
        mock_action.id = action_id
        mock_action.position = 0
        mock_action.action_type = "create_ticket"
        mock_action.description = "desc"
        mock_action.payload = {}
        mock_action.status = WorkflowActionStatus.PENDING
        mock_action.result_url = None
        mock_action.error = None

        run = _make_run(
            user_id=user_id,
            status=WorkflowStatus.AWAITING_CONFIRMATION,
            actions=[mock_action],
        )

        # exec is called twice: once in execute, once in the re-fetch after commit
        session = AsyncMock()
        call_count = 0

        async def exec_side_effect(_query):
            nonlocal call_count
            call_count += 1
            mock_result = AsyncMock()
            mock_result.first = MagicMock(return_value=run)
            return mock_result

        session.exec = exec_side_effect
        session.get = AsyncMock(return_value=None)
        session.add = MagicMock()
        session.commit = AsyncMock()
        session.refresh = AsyncMock()

        from backend.api.dependencies.get_current_user import get_current_user
        from common.database.postgres_models import User

        mock_user = MagicMock(spec=User)
        mock_user.id = user_id

        app.dependency_overrides[get_session] = _session_override(session)
        app.dependency_overrides[get_current_user] = lambda: mock_user
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    f"/workflows/{run.id}/execute",
                    json={"decisions": [{"action_id": str(action_id), "approved": True}]},
                )
        finally:
            app.dependency_overrides.pop(get_session, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 200
        assert mock_action.status == WorkflowActionStatus.APPROVED
        mock_queue.assert_called_once()
        from common.types import TaskType

        assert mock_queue.call_args[0][0].type == TaskType.WORKFLOW_EXECUTE


@pytest.mark.asyncio(loop_scope="session")
class TestDeleteWorkflowRun:
    async def test_delete_returns_204(self, mock_queue):  # noqa: ARG002
        user_id = uuid.uuid4()
        run = _make_run(user_id=user_id)

        from backend.api.dependencies.get_current_user import get_current_user
        from common.database.postgres_models import User

        mock_user = MagicMock(spec=User)
        mock_user.id = user_id

        session = _mock_session(get_result=run)
        app.dependency_overrides[get_session] = _session_override(session)
        app.dependency_overrides[get_current_user] = lambda: mock_user
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(f"/workflows/{run.id}")
        finally:
            app.dependency_overrides.pop(get_session, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 204
        session.delete.assert_called_once_with(run)
        session.commit.assert_called_once()

    async def test_other_user_gets_404(self, mock_queue):  # noqa: ARG002
        other_user_id = uuid.uuid4()
        run = _make_run(user_id=other_user_id)

        from backend.api.dependencies.get_current_user import get_current_user
        from common.database.postgres_models import User

        mock_user = MagicMock(spec=User)
        mock_user.id = uuid.uuid4()  # different user

        session = _mock_session(get_result=run)
        app.dependency_overrides[get_session] = _session_override(session)
        app.dependency_overrides[get_current_user] = lambda: mock_user
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.delete(f"/workflows/{run.id}")
        finally:
            app.dependency_overrides.pop(get_session, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 404
