"""Unit tests for RayWorkflowService dispatch logic.

Tests run without Ray or a real database — all external dependencies are mocked.
The service methods are extracted from the actor class and invoked directly.
"""

import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.database.postgres_models import WorkflowActionStatus, WorkflowRun, WorkflowStatus
from common.types import TaskType, WorkerMessage, WorkflowMessageData
from common.workflows.workflow_manager import WorkflowNotFoundError

# Patch worker.healthcheck before it is imported so HEARTBEAT_DIR.mkdir() never
# touches the filesystem.  This must happen at module level, before any import
# of worker.ray_workflow_service.
_mock_healthcheck = MagicMock()
_mock_healthcheck.HEARTBEAT_DIR = Path("/tmp/test_heartbeat")  # noqa: S108
sys.modules.setdefault("worker.healthcheck", _mock_healthcheck)


def _make_message(task_type: TaskType, run_id: uuid.UUID) -> WorkerMessage:
    return WorkerMessage(
        id=uuid.uuid4(),
        type=task_type,
        data=WorkflowMessageData(workflow_run_id=run_id),
    )


def _make_service():
    """Return a WorkflowServiceCore instance with a mock queue."""
    from worker.ray_workflow_service import WorkflowServiceCore

    queue_service = MagicMock()
    svc = WorkflowServiceCore(queue_service=queue_service)
    return svc, queue_service


class TestHandlePrepare:
    @pytest.mark.asyncio
    async def test_sets_status_awaiting_confirmation_on_success(self):
        svc, queue_service = _make_service()
        run_id = uuid.uuid4()
        message = _make_message(TaskType.WORKFLOW_PREPARE, run_id)
        receipt = MagicMock()

        mock_run = MagicMock(spec=WorkflowRun)
        mock_run.id = run_id
        mock_run.workflow_name = "fake"
        mock_run.actions = []

        mock_action = MagicMock()

        mock_workflow = MagicMock()
        mock_workflow.prepare = AsyncMock(return_value=[mock_action])

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = mock_run

        with (
            patch("worker.ray_workflow_service.SessionLocal", return_value=mock_session),
            patch("worker.ray_workflow_service.WorkflowManager.get_workflow", return_value=mock_workflow),
        ):
            await svc.handle_prepare(message, receipt)

        assert mock_run.status == WorkflowStatus.AWAITING_CONFIRMATION
        mock_session.add.assert_called_once_with(mock_action)
        assert mock_action.workflow_run_id == run_id
        queue_service.complete_message.assert_called_once_with(receipt)
        queue_service.deadletter_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_sets_status_failed_for_unknown_workflow(self):
        svc, queue_service = _make_service()
        run_id = uuid.uuid4()
        message = _make_message(TaskType.WORKFLOW_PREPARE, run_id)
        receipt = MagicMock()

        mock_run = MagicMock(spec=WorkflowRun)
        mock_run.workflow_name = "nonexistent"

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = mock_run

        with (
            patch("worker.ray_workflow_service.SessionLocal", return_value=mock_session),
            patch(
                "worker.ray_workflow_service.WorkflowManager.get_workflow",
                side_effect=WorkflowNotFoundError("nonexistent"),
            ),
        ):
            await svc.handle_prepare(message, receipt)

        assert mock_run.status == WorkflowStatus.FAILED
        assert "nonexistent" in mock_run.error
        queue_service.deadletter_message.assert_called_once_with(message, receipt)
        queue_service.complete_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_deadletters_message_on_prepare_exception(self):
        svc, queue_service = _make_service()
        run_id = uuid.uuid4()
        message = _make_message(TaskType.WORKFLOW_PREPARE, run_id)
        receipt = MagicMock()

        mock_run = MagicMock(spec=WorkflowRun)
        mock_run.workflow_name = "fake"

        mock_workflow = MagicMock()
        mock_workflow.prepare = AsyncMock(side_effect=RuntimeError("LLM exploded"))

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = mock_run

        with (
            patch("worker.ray_workflow_service.SessionLocal", return_value=mock_session),
            patch("worker.ray_workflow_service.WorkflowManager.get_workflow", return_value=mock_workflow),
        ):
            await svc.handle_prepare(message, receipt)

        assert mock_run.status == WorkflowStatus.FAILED
        assert "LLM exploded" in mock_run.error
        queue_service.deadletter_message.assert_called_once_with(message, receipt)

    @pytest.mark.asyncio
    async def test_completes_message_when_run_not_found(self):
        svc, queue_service = _make_service()
        run_id = uuid.uuid4()
        message = _make_message(TaskType.WORKFLOW_PREPARE, run_id)
        receipt = MagicMock()

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = None

        with patch("worker.ray_workflow_service.SessionLocal", return_value=mock_session):
            await svc.handle_prepare(message, receipt)

        queue_service.complete_message.assert_called_once_with(receipt)
        queue_service.deadletter_message.assert_not_called()


class TestHandleExecute:
    @pytest.mark.asyncio
    async def test_marks_approved_actions_completed(self):
        svc, queue_service = _make_service()
        run_id = uuid.uuid4()
        message = _make_message(TaskType.WORKFLOW_EXECUTE, run_id)
        receipt = MagicMock()

        action1 = MagicMock()
        action1.status = WorkflowActionStatus.APPROVED
        action2 = MagicMock()
        action2.status = WorkflowActionStatus.REJECTED  # should be skipped

        mock_run = MagicMock(spec=WorkflowRun)
        mock_run.workflow_name = "fake"
        mock_run.actions = [action1, action2]

        mock_workflow = MagicMock()
        mock_workflow.execute_action = AsyncMock(return_value="https://github.com/issue/1")

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = mock_run

        with (
            patch("worker.ray_workflow_service.SessionLocal", return_value=mock_session),
            patch("worker.ray_workflow_service.WorkflowManager.get_workflow", return_value=mock_workflow),
        ):
            await svc.handle_execute(message, receipt)

        assert action1.status == WorkflowActionStatus.COMPLETED
        assert action1.result_url == "https://github.com/issue/1"
        # Rejected action should be untouched
        assert action2.status == WorkflowActionStatus.REJECTED
        assert mock_run.status == WorkflowStatus.COMPLETED
        queue_service.complete_message.assert_called_once_with(receipt)

    @pytest.mark.asyncio
    async def test_marks_action_failed_and_continues_on_action_exception(self):
        svc, queue_service = _make_service()
        run_id = uuid.uuid4()
        message = _make_message(TaskType.WORKFLOW_EXECUTE, run_id)
        receipt = MagicMock()

        action1 = MagicMock()
        action1.id = uuid.uuid4()
        action1.status = WorkflowActionStatus.APPROVED
        action2 = MagicMock()
        action2.id = uuid.uuid4()
        action2.status = WorkflowActionStatus.APPROVED

        mock_run = MagicMock(spec=WorkflowRun)
        mock_run.workflow_name = "fake"
        mock_run.actions = [action1, action2]

        mock_workflow = MagicMock()
        # First action raises, second succeeds
        mock_workflow.execute_action = AsyncMock(
            side_effect=[RuntimeError("GitHub API down"), "https://github.com/issue/2"]
        )

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = mock_run

        with (
            patch("worker.ray_workflow_service.SessionLocal", return_value=mock_session),
            patch("worker.ray_workflow_service.WorkflowManager.get_workflow", return_value=mock_workflow),
        ):
            await svc.handle_execute(message, receipt)

        assert action1.status == WorkflowActionStatus.FAILED
        assert "GitHub API down" in action1.error
        assert action2.status == WorkflowActionStatus.COMPLETED
        # Overall run still completes (per-action errors don't fail the run)
        assert mock_run.status == WorkflowStatus.COMPLETED
        queue_service.complete_message.assert_called_once_with(receipt)

    @pytest.mark.asyncio
    async def test_sets_run_failed_on_unexpected_execute_exception(self):
        svc, queue_service = _make_service()
        run_id = uuid.uuid4()
        message = _make_message(TaskType.WORKFLOW_EXECUTE, run_id)
        receipt = MagicMock()

        mock_run = MagicMock(spec=WorkflowRun)
        mock_run.workflow_name = "fake"
        # Accessing run.actions raises unexpectedly
        type(mock_run).actions = property(MagicMock(side_effect=RuntimeError("DB connection lost")))

        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.get.return_value = mock_run

        mock_workflow = MagicMock()

        with (
            patch("worker.ray_workflow_service.SessionLocal", return_value=mock_session),
            patch("worker.ray_workflow_service.WorkflowManager.get_workflow", return_value=mock_workflow),
        ):
            await svc.handle_execute(message, receipt)

        assert mock_run.status == WorkflowStatus.FAILED
        queue_service.deadletter_message.assert_called_once_with(message, receipt)
        queue_service.complete_message.assert_not_called()


def test_worker_message_workflow_prepare_roundtrip():
    run_id = uuid.uuid4()
    msg = WorkerMessage(
        id=uuid.uuid4(),
        type=TaskType.WORKFLOW_PREPARE,
        data=WorkflowMessageData(workflow_run_id=run_id),
    )
    serialised = msg.model_dump_json()
    restored = WorkerMessage.model_validate_json(serialised)

    assert restored.type == TaskType.WORKFLOW_PREPARE
    assert isinstance(restored.data, WorkflowMessageData)
    assert restored.data.workflow_run_id == run_id


def test_worker_message_workflow_execute_roundtrip():
    run_id = uuid.uuid4()
    msg = WorkerMessage(
        id=uuid.uuid4(),
        type=TaskType.WORKFLOW_EXECUTE,
        data=WorkflowMessageData(workflow_run_id=run_id),
    )
    restored = WorkerMessage.model_validate_json(msg.model_dump_json())

    assert restored.type == TaskType.WORKFLOW_EXECUTE
    assert restored.data.workflow_run_id == run_id
