"""Base Protocol for all agentic workflows."""

from typing import Protocol, runtime_checkable

from common.database.postgres_models import WorkflowAction, WorkflowRun


@runtime_checkable
class Workflow(Protocol):
    name: str
    display_name: str
    description: str
    config_schema: dict

    @classmethod
    async def prepare(cls, run: WorkflowRun) -> list[WorkflowAction]:
        """
        Read-only stage.

        Fetch external state, call the LLM, return a list of proposed WorkflowAction
        objects (not yet persisted). Must not mutate any external system.
        """
        ...

    @classmethod
    async def execute_action(cls, action: WorkflowAction) -> str | None:
        """
        Execute a single approved action.

        Returns the result URL (e.g. the GitHub issue URL) or None.
        Raises on failure; the caller catches and marks the action as FAILED.
        """
        ...
