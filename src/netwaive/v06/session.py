from __future__ import annotations

import uuid
from dataclasses import dataclass

from .contracts import PendingPlan


@dataclass
class SessionScope:
    session_id: str
    generation: str
    pending: PendingPlan | None = None

    @classmethod
    def new(cls, session_id: str | None = None) -> "SessionScope":
        return cls(session_id or str(uuid.uuid4()), str(uuid.uuid4()))

    def begin_request(self) -> str:
        self.generation = str(uuid.uuid4())
        self.pending = None
        return self.generation

    def store(self, plan: PendingPlan) -> None:
        if plan.session_id != self.session_id or plan.generation != self.generation:
            raise ValueError("Plan hors scope de session.")
        self.pending = plan

    def consume(self, fingerprint: str) -> PendingPlan:
        plan = self.pending
        if plan is None or plan.fingerprint != fingerprint:
            raise ValueError("Aucun plan confirmable dans cette session.")
        self.pending = None
        return plan
