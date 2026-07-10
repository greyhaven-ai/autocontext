"""Per-scenario evaluator-epoch lifecycle registry (AC-885 Slice C).

One ACTIVE evaluator epoch per scenario. observe() is the mechanical trigger: the first epoch a
scenario ever sees auto-activates (bootstrap); a subsequent, different epoch is registered as a
candidate (its scores are quarantined until promoted). Mirrors ModelRegistry's file-per-record
JSON store and its demote-not-delete rollback. See docs/ac-885-slice-c-epoch-lifecycle-design.md.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from autocontext.execution.evaluator_epoch import EvaluatorEpoch

_VALID_STATES = frozenset({"candidate", "active", "disabled"})


def _default_now() -> str:
    return datetime.now(UTC).isoformat()


def observe_epoch_quarantined(root: Path, scenario: str, epoch_id: str | None) -> bool | None:
    """Observe ``epoch_id`` for ``scenario`` and report whether its scores are quarantined.

    Shared by the agent-task score write sites. Returns ``None`` when ``epoch_id`` is ``None``
    (tournament/no-judge: there is nothing to observe). Otherwise it records/bootstraps the epoch via
    :meth:`EvaluatorEpochRegistry.observe_id` and returns ``True`` when the epoch is not the
    scenario's active one: a candidate or disabled epoch's scores are quarantined, the bootstrap or
    active epoch's are not.
    """
    if epoch_id is None:
        return None
    registry = EvaluatorEpochRegistry(root)
    record = registry.observe_id(scenario, epoch_id)
    return record.activation_state != "active"


class EvaluatorEpochRecord(BaseModel):
    scenario: str
    epoch_id: str
    rubric_hash: str
    judge_provider: str
    judge_model: str
    activation_state: str
    created_at: str
    promotion: dict[str, Any] | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.activation_state not in _VALID_STATES:
            raise ValueError(f"Invalid activation_state {self.activation_state!r}; expected {sorted(_VALID_STATES)}")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvaluatorEpochRecord:
        return cls(**data)


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


class EvaluatorEpochRegistry:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _scenario_dir(self, scenario: str) -> Path:
        return self.root / _safe(scenario)

    def _path(self, scenario: str, epoch_id: str) -> Path:
        return self._scenario_dir(scenario) / f"{epoch_id}.json"

    def register(self, record: EvaluatorEpochRecord) -> Path:
        path = self._path(record.scenario, record.epoch_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
        return path

    def load(self, scenario: str, epoch_id: str) -> EvaluatorEpochRecord | None:
        path = self._path(scenario, epoch_id)
        if not path.exists():
            return None
        return EvaluatorEpochRecord.from_dict(json.loads(path.read_text()))

    def list_for_scenario(self, scenario: str) -> list[EvaluatorEpochRecord]:
        out: list[EvaluatorEpochRecord] = []
        for path in self._scenario_dir(scenario).glob("*.json"):
            out.append(EvaluatorEpochRecord.from_dict(json.loads(path.read_text())))
        return out

    def active_for(self, scenario: str) -> EvaluatorEpochRecord | None:
        for rec in self.list_for_scenario(scenario):
            if rec.activation_state == "active":
                return rec
        return None

    def activate(self, scenario: str, epoch_id: str) -> None:
        """Promote ``epoch_id`` to active, demoting any prior active to disabled.

        Load the target FIRST: if the epoch does not exist for this scenario, leave all state
        unchanged (no-op) so a bad id can never leave the scenario with zero active epochs.
        """
        target = self.load(scenario, epoch_id)
        if target is None:
            return
        current = self.active_for(scenario)
        if current is not None and current.epoch_id != epoch_id:
            current.activation_state = "disabled"
            self.register(current)
        target.activation_state = "active"
        self.register(target)

    def observe(
        self,
        scenario: str,
        epoch: EvaluatorEpoch,
        *,
        now_fn: Callable[[], str] = _default_now,
    ) -> EvaluatorEpochRecord:
        """Record an observed epoch. First epoch for a scenario auto-activates; a new epoch mints a
        candidate; a known epoch is returned unchanged."""
        existing = self.load(scenario, epoch.epoch_id)
        if existing is not None:
            return existing
        state = "active" if self.active_for(scenario) is None else "candidate"
        record = EvaluatorEpochRecord(
            scenario=scenario,
            epoch_id=epoch.epoch_id,
            rubric_hash=epoch.rubric_hash,
            judge_provider=epoch.judge_provider,
            judge_model=epoch.judge_model,
            activation_state=state,
            created_at=now_fn(),
        )
        self.register(record)
        return record

    def observe_id(
        self,
        scenario: str,
        epoch_id: str,
        *,
        now_fn: Callable[[], str] = _default_now,
    ) -> EvaluatorEpochRecord:
        """Run the same mechanical trigger as :meth:`observe`, keyed only on ``epoch_id``.

        Used at score write sites where only the epoch id string is authoritative (the judge
        internals that produced it are not recomputed, since a BEFORE_JUDGE hook may have mutated
        them). The rubric_hash/judge_provider/judge_model are stored empty and backfilled in Slice C2
        when calibration runs. First id for a scenario auto-activates; a new id mints a candidate; a
        known id is returned unchanged.
        """
        existing = self.load(scenario, epoch_id)
        if existing is not None:
            return existing
        state = "active" if self.active_for(scenario) is None else "candidate"
        record = EvaluatorEpochRecord(
            scenario=scenario,
            epoch_id=epoch_id,
            rubric_hash="",
            judge_provider="",
            judge_model="",
            activation_state=state,
            created_at=now_fn(),
        )
        self.register(record)
        return record
