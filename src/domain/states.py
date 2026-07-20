"""In-memory state transitions for one MVP editorial run."""

from enum import Enum


class WorkflowState(str, Enum):
    CREATED = "created"
    COLLECTED = "collected"
    ANALYZED = "analyzed"
    SELECTED = "selected"
    COMPLETED = "completed"
    FAILED = "failed"


_ALLOWED_TRANSITIONS = {
    WorkflowState.CREATED: {WorkflowState.COLLECTED, WorkflowState.FAILED},
    WorkflowState.COLLECTED: {WorkflowState.ANALYZED, WorkflowState.FAILED},
    WorkflowState.ANALYZED: {WorkflowState.SELECTED, WorkflowState.FAILED},
    WorkflowState.SELECTED: {WorkflowState.COMPLETED, WorkflowState.FAILED},
    WorkflowState.COMPLETED: set(),
    WorkflowState.FAILED: set(),
}


def transition_to(current: WorkflowState, target: WorkflowState) -> WorkflowState:
    """Validate and return one forward-only workflow transition."""
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Invalid workflow transition: {current.value} -> {target.value}")
    return target
