"""charter proposals: structured diffs the advisor emits and the control surface approves."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from autocontext.ambient.charter import Charter, CharterBudgets, CharterTarget


class ProposalError(Exception):
    pass


class CharterProposal(BaseModel):
    proposal_id: str = Field(min_length=1)
    kind: Literal["add_target", "update_budgets"]
    payload: dict[str, Any]
    rationale: str = Field(min_length=1)
    status: Literal["pending", "applied", "rejected"] = "pending"


def apply_proposal(charter: Charter, proposal: CharterProposal) -> Charter:
    # rebuild through full validation rather than model_copy(update=...),
    # which skips validators by pydantic design and would let a malformed
    # proposal sidestep the charter's guardrail and schema checks.
    data = charter.model_dump(mode="json")
    if proposal.kind == "add_target":
        target = CharterTarget(**proposal.payload)
        if any(existing.name == target.name for existing in charter.targets):
            raise ProposalError(f"target {target.name} already exists")
        data["targets"] = [*data["targets"], target.model_dump(mode="json")]
        return Charter(**data)
    data["budgets"] = CharterBudgets(**proposal.payload).model_dump(mode="json")
    return Charter(**data)


class ProposalStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _read_all(self) -> dict[str, CharterProposal]:
        records: dict[str, CharterProposal] = {}
        if not self.path.exists():
            return records
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            proposal = CharterProposal(**json.loads(line))
            records[proposal.proposal_id] = proposal
        return records

    def append(self, proposal: CharterProposal) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(proposal.model_dump(mode="json")) + "\n")

    def pending(self) -> list[CharterProposal]:
        return [p for p in self._read_all().values() if p.status == "pending"]

    def mark(self, proposal_id: str, status: Literal["applied", "rejected"]) -> None:
        records = self._read_all()
        if proposal_id not in records:
            raise ProposalError(f"unknown proposal: {proposal_id}")
        updated = records[proposal_id].model_copy(update={"status": status})
        self.append(updated)
