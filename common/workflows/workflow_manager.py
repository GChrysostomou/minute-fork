"""Auto-discovery registry for Workflow implementations."""

import importlib
import importlib.util
import inspect
import logging
import pkgutil
import typing
from pathlib import Path

from common.types import WorkflowMetadata
from common.workflows.base_workflow import Workflow

logger = logging.getLogger(__name__)

# Resolve the workflows package path without importing the package itself,
# to avoid a circular import with __init__.py.
_WORKFLOWS_PACKAGE_NAME = "common.workflows"
_WORKFLOWS_PACKAGE_PATH = [str(Path(__file__).parent)]


class WorkflowNotFoundError(KeyError):
    pass


class WorkflowManager:
    _registry: typing.ClassVar[dict[str, Workflow]] = {}

    @classmethod
    def register_workflow(cls, workflow: Workflow) -> None:
        if workflow.name in cls._registry:
            msg = f"Duplicate workflow name: {workflow.name!r}"
            raise ValueError(msg)
        cls._registry[workflow.name] = workflow
        logger.info("Registered workflow: %s", workflow.name)

    @classmethod
    def discover_workflows(cls) -> None:
        """Walk common/workflows/, import every module, and register classes that
        satisfy the Workflow Protocol. Mirrors TemplateManager.discover_templates().
        """
        package_path = _WORKFLOWS_PACKAGE_PATH
        package_name = _WORKFLOWS_PACKAGE_NAME

        for _, modname, _ in pkgutil.walk_packages(package_path, package_name + "."):
            if modname.endswith(("base_workflow", "workflow_manager")):
                continue
            try:
                module = importlib.import_module(modname)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to import workflow module %s", modname, exc_info=True)
                continue

            for _, obj in inspect.getmembers(module, inspect.isclass):
                if obj.__module__ != modname:
                    continue
                if not all(
                    hasattr(obj, attr)
                    for attr in typing._get_protocol_attrs(Workflow)  # noqa: SLF001
                ):
                    continue
                try:
                    cls.register_workflow(obj)
                except Exception:  # noqa: BLE001
                    logger.warning("Failed to register workflow %s", obj, exc_info=True)

    @classmethod
    def get_workflow(cls, name: str) -> type:
        try:
            return cls._registry[name]
        except KeyError:
            msg = f"No workflow registered with name: {name!r}"
            raise WorkflowNotFoundError(msg) from None

    @classmethod
    def list_workflows(cls) -> list[WorkflowMetadata]:
        return [
            WorkflowMetadata(
                name=w.name,
                display_name=w.display_name,
                description=w.description,
                config_schema=w.config_schema,
            )
            for w in cls._registry.values()
        ]

    @classmethod
    def reset(cls) -> None:
        """Test helper — clears the registry between tests."""
        cls._registry = {}


WorkflowManager.discover_workflows()
