from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


WorkflowState = Literal[
    "intake",
    "evidence_extracted",
    "ee_confirmed",
    "layout_reviewed",
    "approved_for_staging",
    "generated",
    "rejected",
]


ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "intake": {"evidence_extracted", "rejected"},
    "evidence_extracted": {"ee_confirmed", "rejected"},
    "ee_confirmed": {"layout_reviewed", "rejected"},
    "layout_reviewed": {"approved_for_staging", "rejected"},
    "approved_for_staging": {"generated", "rejected"},
    "generated": set(),
    "rejected": {"intake"},
}


class ReviewEvent(BaseModel):
    from_state: WorkflowState
    to_state: WorkflowState
    actor_role: Literal["system", "EE", "Layout", "LibraryOwner"]
    actor: str
    timestamp: str
    note: str


class ReviewWorkflow(BaseModel):
    state: WorkflowState = "intake"
    events: list[ReviewEvent] = Field(default_factory=list)

    def transition(self, to_state: WorkflowState, actor_role: str, actor: str, note: str) -> None:
        if to_state not in ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(f"不允许从 {self.state} 转换到 {to_state}")
        required_roles = {
            "ee_confirmed": "EE",
            "layout_reviewed": "Layout",
            "approved_for_staging": "LibraryOwner",
        }
        required = required_roles.get(to_state)
        if required and actor_role != required:
            raise ValueError(f"状态 {to_state} 必须由 {required} 执行")
        event = ReviewEvent(
            from_state=self.state,
            to_state=to_state,
            actor_role=actor_role,
            actor=actor,
            timestamp=datetime.now(timezone.utc).isoformat(),
            note=note,
        )
        self.events.append(event)
        self.state = to_state

