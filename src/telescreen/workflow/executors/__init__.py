"""Workflow executor registry and base interfaces."""

from __future__ import annotations

from dataclasses import dataclass, field

from telescreen.core.errors import TelescreenError
from telescreen.core.vocabulary import ErrorCode, StepType
from telescreen.workflow.step_api import StepExecutor


@dataclass
class ExecutorRegistry:
    """Register step executors and resolve them by DSL step type."""

    executors: list[tuple[StepType, StepExecutor]] = field(default_factory=list)

    def register(self, executor: StepExecutor) -> None:
        """Register an executor, replacing a previous executor of the same type."""
        self.executors = [
            (step_type, registered)
            for step_type, registered in self.executors
            if step_type is not executor.step_type
        ]
        self.executors.append((executor.step_type, executor))

    def get(self, step_type: StepType) -> StepExecutor:
        """Return an executor or raise a safe workflow configuration error."""
        executor: StepExecutor | None = next(
            (item for registered, item in self.executors if registered is step_type),
            None,
        )
        if executor is None:
            raise TelescreenError(ErrorCode.WORKFLOW_INVALID, "workflow executor unavailable")
        return executor
