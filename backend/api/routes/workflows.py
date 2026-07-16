"""Workflow API routes."""

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy.orm import selectinload
from sqlmodel import select

from backend.api.dependencies import SQLSessionDep, UserDep
from common.database.postgres_models import (
    Transcription,
    WorkflowAction,
    WorkflowActionStatus,
    WorkflowRun,
    WorkflowStatus,
)
from common.services.queue_services import get_queue_service
from common.settings import get_settings
from common.types import (
    TaskType,
    WorkerMessage,
    WorkflowActionResponse,
    WorkflowExecuteRequest,
    WorkflowMessageData,
    WorkflowMetadata,
    WorkflowRunCreateRequest,
    WorkflowRunResponse,
)
from common.workflows import WorkflowManager
from common.workflows.workflow_manager import WorkflowNotFoundError

settings = get_settings()

llm_queue_service = get_queue_service(
    settings.QUEUE_SERVICE_NAME, settings.LLM_QUEUE_NAME, settings.LLM_DEADLETTER_QUEUE_NAME
)

workflows_router = APIRouter(tags=["Workflows"])


def _action_response(action: WorkflowAction) -> WorkflowActionResponse:
    return WorkflowActionResponse(
        id=action.id,
        position=action.position,
        action_type=action.action_type,
        description=action.description,
        payload=action.payload,
        status=action.status,
        result_url=action.result_url,
        error=action.error,
    )


def _run_response(run: WorkflowRun) -> WorkflowRunResponse:
    return WorkflowRunResponse(
        id=run.id,
        transcription_id=run.transcription_id,
        workflow_name=run.workflow_name,
        status=run.status,
        error=run.error,
        created_datetime=run.created_datetime,
        updated_datetime=run.updated_datetime,
        actions=[_action_response(a) for a in run.actions],
    )


async def _get_run_with_actions(run_id: uuid.UUID, session: SQLSessionDep) -> WorkflowRun | None:
    """Load a WorkflowRun with its actions eagerly to avoid lazy-load issues."""
    result = await session.exec(
        select(WorkflowRun).where(WorkflowRun.id == run_id).options(selectinload(WorkflowRun.actions))  # type: ignore[arg-type]
    )
    return result.first()


# --- List available workflow definitions ---
@workflows_router.get("/workflows", response_model=list[WorkflowMetadata])
async def list_workflows(user: UserDep) -> list[WorkflowMetadata]:  # noqa: ARG001
    return WorkflowManager.list_workflows()


# --- Create a new workflow run ---
@workflows_router.post("/workflows", response_model=WorkflowRunResponse, status_code=201)
async def create_workflow_run(
    request: WorkflowRunCreateRequest,
    session: SQLSessionDep,
    user: UserDep,
) -> WorkflowRunResponse:
    try:
        WorkflowManager.get_workflow(request.workflow_name)
    except WorkflowNotFoundError as err:
        raise HTTPException(400, f"Unknown workflow: {request.workflow_name!r}") from err

    transcription = await session.get(Transcription, request.transcription_id)
    if not transcription or transcription.user_id != user.id:
        raise HTTPException(404, "Transcription not found")

    run = WorkflowRun(
        transcription_id=request.transcription_id,
        user_id=user.id,
        workflow_name=request.workflow_name,
        config=request.config,
    )
    session.add(run)
    await session.commit()

    run_id = run.id
    llm_queue_service.publish_message(
        WorkerMessage(
            id=run_id,
            type=TaskType.WORKFLOW_PREPARE,
            data=WorkflowMessageData(workflow_run_id=run_id),
        )
    )
    run = await _get_run_with_actions(run_id, session)
    return _run_response(run)


@workflows_router.get("/workflows/{run_id}", response_model=WorkflowRunResponse)
async def get_workflow_run(
    run_id: uuid.UUID,
    session: SQLSessionDep,
    user: UserDep,
) -> WorkflowRunResponse:
    run = await _get_run_with_actions(run_id, session)
    if not run or run.user_id != user.id:
        raise HTTPException(404, "Not found")
    return _run_response(run)


# --- Submit approve/reject decisions and trigger execution ---
@workflows_router.post("/workflows/{run_id}/execute", response_model=WorkflowRunResponse)
async def execute_workflow_run(
    run_id: uuid.UUID,
    request: WorkflowExecuteRequest,
    session: SQLSessionDep,
    user: UserDep,
) -> WorkflowRunResponse:
    run = await _get_run_with_actions(run_id, session)
    if not run or run.user_id != user.id:
        raise HTTPException(404, "Not found")
    if run.status != WorkflowStatus.AWAITING_CONFIRMATION:
        raise HTTPException(409, "Run is not awaiting confirmation")

    decision_map = {d.action_id: d.approved for d in request.decisions}
    for action in run.actions:
        if action.id in decision_map:
            action.status = WorkflowActionStatus.APPROVED if decision_map[action.id] else WorkflowActionStatus.REJECTED

    run.status = WorkflowStatus.IN_PROGRESS
    await session.commit()
    await session.refresh(run)

    llm_queue_service.publish_message(
        WorkerMessage(
            id=run.id,
            type=TaskType.WORKFLOW_EXECUTE,
            data=WorkflowMessageData(workflow_run_id=run.id),
        )
    )

    run = await _get_run_with_actions(run_id, session)
    return _run_response(run)


# --- Delete / cancel a run ---
@workflows_router.delete("/workflows/{run_id}", status_code=204)
async def delete_workflow_run(
    run_id: uuid.UUID,
    session: SQLSessionDep,
    user: UserDep,
) -> None:
    run = await session.get(WorkflowRun, run_id)
    if not run or run.user_id != user.id:
        raise HTTPException(404, "Not found")
    await session.delete(run)
    await session.commit()
