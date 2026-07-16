"""Ray actor for agentic workflow task processing."""

import asyncio
import logging
from typing import Any

import ray

from common.database.postgres_database import SessionLocal
from common.database.postgres_models import WorkflowActionStatus, WorkflowRun, WorkflowStatus
from common.services.queue_services.base import QueueService
from common.types import TaskType, WorkerMessage, WorkflowMessageData
from common.workflows.workflow_manager import WorkflowManager, WorkflowNotFoundError
from worker.healthcheck import HEARTBEAT_DIR

logger = logging.getLogger(__name__)


class WorkflowServiceCore:
    """Pure-Python workflow dispatch logic, extracted so it can be unit-tested
    without Ray or a real database.  ``RayWorkflowService`` delegates to this class.
    """

    def __init__(self, queue_service: QueueService) -> None:
        self.queue_service = queue_service

    # ------------------------------------------------------------------
    # PREPARE: fetch external state + LLM proposal → WorkflowAction rows
    # ------------------------------------------------------------------
    async def handle_prepare(self, message: WorkerMessage, receipt_handle: Any) -> None:
        data: WorkflowMessageData = message.data
        with SessionLocal() as session:
            run = session.get(WorkflowRun, data.workflow_run_id)
            if not run:
                logger.error("WorkflowRun %s not found", data.workflow_run_id)
                self.queue_service.complete_message(receipt_handle)
                return

            run.status = WorkflowStatus.IN_PROGRESS
            session.commit()

            try:
                workflow_cls = WorkflowManager.get_workflow(run.workflow_name)
                proposed_actions = await workflow_cls.prepare(run)

                for action in proposed_actions:
                    action.workflow_run_id = run.id
                    session.add(action)

                run.status = WorkflowStatus.AWAITING_CONFIRMATION
                session.commit()
                self.queue_service.complete_message(receipt_handle)

            except WorkflowNotFoundError:
                run.status = WorkflowStatus.FAILED
                run.error = f"Unknown workflow: {run.workflow_name!r}"
                session.commit()
                self.queue_service.deadletter_message(message, receipt_handle)

            except Exception as exc:
                logger.exception("WORKFLOW_PREPARE failed for run %s", run.id)
                run.status = WorkflowStatus.FAILED
                run.error = str(exc)
                session.commit()
                self.queue_service.deadletter_message(message, receipt_handle)

    # ------------------------------------------------------------------
    # EXECUTE: apply each approved action sequentially
    # ------------------------------------------------------------------
    async def handle_execute(self, message: WorkerMessage, receipt_handle: Any) -> None:
        data: WorkflowMessageData = message.data
        with SessionLocal() as session:
            run = session.get(WorkflowRun, data.workflow_run_id)
            if not run:
                logger.error("WorkflowRun %s not found", data.workflow_run_id)
                self.queue_service.complete_message(receipt_handle)
                return

            run.status = WorkflowStatus.IN_PROGRESS
            session.commit()

            try:
                workflow_cls = WorkflowManager.get_workflow(run.workflow_name)
                approved = [a for a in run.actions if a.status == WorkflowActionStatus.APPROVED]

                for action in approved:
                    try:
                        result_url = await workflow_cls.execute_action(action)
                        action.status = WorkflowActionStatus.COMPLETED
                        action.result_url = result_url
                    except Exception as exc:
                        logger.exception("Action %s failed", action.id)
                        action.status = WorkflowActionStatus.FAILED
                        action.error = str(exc)
                    session.commit()

                run.status = WorkflowStatus.COMPLETED
                session.commit()
                self.queue_service.complete_message(receipt_handle)

            except Exception as exc:
                logger.exception("WORKFLOW_EXECUTE failed for run %s", run.id)
                run.status = WorkflowStatus.FAILED
                run.error = str(exc)
                session.commit()
                self.queue_service.deadletter_message(message, receipt_handle)


# restart indefinitely, try each task only once
@ray.remote(max_restarts=-1, max_task_retries=0)
class RayWorkflowService:
    def __init__(self, queue_service: QueueService, stopped) -> None:
        self.stopped = stopped
        self._core = WorkflowServiceCore(queue_service)
        actor_id = ray.get_runtime_context().get_actor_id()
        self.heartbeat_path = HEARTBEAT_DIR / f"worker_{actor_id}.heartbeat"
        self.heartbeat_path.touch()
        logger.info("Ray Workflow receive service initialised")

    async def process(self) -> None:
        while not await self.stopped.get.remote():
            logger.info("Receiving workflow messages")
            messages = self._core.queue_service.receive_message(max_messages=5)
            tasks: list[asyncio.Task] = []
            for message, receipt_handle in messages:
                match message.type:
                    case TaskType.WORKFLOW_PREPARE:
                        tasks.append(asyncio.create_task(self._core.handle_prepare(message, receipt_handle)))
                    case TaskType.WORKFLOW_EXECUTE:
                        tasks.append(asyncio.create_task(self._core.handle_execute(message, receipt_handle)))
                    case _:
                        logger.warning("RayWorkflowService: unexpected task type %s", message.type)
                        self._core.queue_service.deadletter_message(message, receipt_handle)
            if tasks:
                done, _ = await asyncio.wait(tasks)
                for task in done:
                    try:
                        task.result()
                    except Exception:
                        logger.exception("Unhandled error in workflow actor")
            self.heartbeat_path.touch()
