"""Unit tests for WorkflowManager auto-discovery and registry."""

import pytest

from common.workflows.workflow_manager import WorkflowManager, WorkflowNotFoundError


@pytest.fixture(autouse=True)
def reset_registry():
    """Isolate each test from side-effects of auto-discovery."""
    WorkflowManager.reset()
    yield
    WorkflowManager.reset()


class FakeWorkflow:
    name = "fake"
    display_name = "Fake Workflow"
    description = "A fake workflow for testing."
    config_schema = {"type": "object", "properties": {}}  # noqa: RUF012

    @classmethod
    async def prepare(cls, _):
        return []

    @classmethod
    async def execute_action(cls, _):
        return None


def test_register_and_retrieve():
    WorkflowManager.register_workflow(FakeWorkflow)
    assert WorkflowManager.get_workflow("fake") is FakeWorkflow


def test_list_workflows_returns_metadata():
    WorkflowManager.register_workflow(FakeWorkflow)
    items = WorkflowManager.list_workflows()
    assert len(items) == 1
    assert items[0].name == "fake"
    assert items[0].display_name == "Fake Workflow"


def test_get_unknown_workflow_raises():
    with pytest.raises(WorkflowNotFoundError):
        WorkflowManager.get_workflow("nonexistent")


def test_duplicate_name_raises():
    WorkflowManager.register_workflow(FakeWorkflow)
    with pytest.raises(ValueError, match="Duplicate workflow name"):
        WorkflowManager.register_workflow(FakeWorkflow)


def test_reset_clears_registry():
    WorkflowManager.register_workflow(FakeWorkflow)
    WorkflowManager.reset()
    assert WorkflowManager.list_workflows() == []
