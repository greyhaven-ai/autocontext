"""Per-scenario evaluator-epoch lifecycle registry (AC-885 Slice C).

One ACTIVE evaluator epoch per scenario. observe() is the mechanical trigger: the first epoch a
scenario ever sees auto-activates (bootstrap); a subsequent, different epoch is registered as a
candidate (its scores are quarantined until promoted). Mirrors ModelRegistry's file-per-record
JSON store and its demote-not-delete rollback. See docs/ac-885-slice-c-epoch-lifecycle-design.md.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from autocontext.execution.evaluator_epoch import EvaluatorEpoch

logger = logging.getLogger(__name__)

_VALID_STATES = frozenset({"candidate", "active", "disabled"})

# Production epoch ids are sha256 hex digests (see execution/evaluator_epoch.py). Anything else at an
# id-keyed write site (``observe_id``) is untrusted input and is refused: it could be a path-traversal
# payload or a corrupt lineage value that must not silently mint a registry record.
_EPOCH_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _require_epoch_id(epoch_id: str) -> None:
    if not _EPOCH_ID_PATTERN.match(epoch_id):
        raise ValueError(f"Invalid epoch_id {epoch_id!r}; expected a 64-character sha256 hex digest")


def _default_now() -> str:
    return datetime.now(UTC).isoformat()


def observe_epoch_quarantined(root: Path, scenario: str, epoch_id: str | None) -> bool | None:
    """Observe ``epoch_id`` for ``scenario`` and report whether its scores are quarantined.

    Shared by the agent-task score write sites. Returns ``None`` when ``epoch_id`` is ``None``
    (tournament/no-judge: there is nothing to observe). Otherwise it records/bootstraps the epoch via
    :meth:`EvaluatorEpochRegistry.observe_id` and returns ``True`` when the epoch is not the
    scenario's active one: a candidate or disabled epoch's scores are quarantined, the bootstrap or
    active epoch's are not.

    Registry IO sits on the score-persist path but never aborts persistence of an otherwise-complete
    run's score. It fails CLOSED: when ``epoch_id`` is non-null but its lifecycle state cannot be
    verified (unwritable root, a corrupt record, an invalid id), the score is conservatively
    quarantined (returns ``True``) rather than trusted. Only ``epoch_id is None`` (tournament/no-judge:
    genuinely nothing to observe) returns ``None``.
    """
    if epoch_id is None:
        return None
    try:
        registry = EvaluatorEpochRegistry(root)
        record = registry.observe_id(scenario, epoch_id)
        return record.activation_state != "active"
    except Exception:
        logger.debug("evaluator_epoch_registry: observe failed for scenario %s", scenario, exc_info=True)
        return True


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


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


class EvaluatorEpochRegistry:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _scenario_dir(self, scenario: str) -> Path:
        return self.root / _safe(scenario)

    def _path(self, scenario: str, epoch_id: str) -> Path:
        """Resolve the record path, enforcing that it stays under the scenario directory.

        Defense in depth against a traversal payload in ``epoch_id`` (``../../escaped``): the
        resolved path must be contained by the scenario directory or this raises ``ValueError``.
        """
        scenario_dir = self._scenario_dir(scenario)
        candidate = (scenario_dir / f"{epoch_id}.json").resolve()
        base = scenario_dir.resolve()
        if candidate != base and not candidate.is_relative_to(base):
            raise ValueError(f"epoch_id {epoch_id!r} resolves outside the scenario directory")
        return candidate

    @contextmanager
    def _scenario_lock(self, scenario: str) -> Iterator[None]:
        """Hold an exclusive per-scenario process lock around a read-decide-write critical section.

        Serializes concurrent bootstraps of the same scenario so two workers cannot both observe "no
        active epoch" and each write an ``active`` record. Not re-entrant within a process: callers
        already holding the lock must use the ``_locked`` helpers.
        """
        lock_path = self.root / f"{_safe(scenario)}.lock"
        with open(lock_path, "w") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def register(self, record: EvaluatorEpochRecord) -> Path:
        path = self._path(record.scenario, record.epoch_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(record.model_dump(), indent=2, ensure_ascii=False)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(tmp_name, path)
        except BaseException:
            with suppress(OSError):
                os.unlink(tmp_name)
            raise
        return path

    def load(self, scenario: str, epoch_id: str) -> EvaluatorEpochRecord | None:
        path = self._path(scenario, epoch_id)
        if not path.exists():
            return None
        return EvaluatorEpochRecord.model_validate(json.loads(path.read_text()))

    def list_for_scenario(self, scenario: str) -> list[EvaluatorEpochRecord]:
        out: list[EvaluatorEpochRecord] = []
        for path in self._scenario_dir(scenario).glob("*.json"):
            out.append(EvaluatorEpochRecord.model_validate(json.loads(path.read_text())))
        return out

    def _active_for_locked(self, scenario: str) -> EvaluatorEpochRecord | None:
        """Return the single active record, self-healing a multiple-active state.

        If more than one active record exists (a legacy or raced write), deterministically keep the
        lexicographically-smallest ``epoch_id`` active and demote the rest to disabled, then return
        the kept one. Assumes the scenario lock is held.
        """
        actives = sorted(
            (rec for rec in self.list_for_scenario(scenario) if rec.activation_state == "active"),
            key=lambda rec: rec.epoch_id,
        )
        if not actives:
            return None
        kept = actives[0]
        for extra in actives[1:]:
            extra.activation_state = "disabled"
            self.register(extra)
        return kept

    def active_for(self, scenario: str) -> EvaluatorEpochRecord | None:
        with self._scenario_lock(scenario):
            return self._active_for_locked(scenario)

    def activate(self, scenario: str, epoch_id: str) -> None:
        """Promote ``epoch_id`` to active, demoting any prior active to disabled.

        Load the target FIRST: if the epoch does not exist for this scenario, leave all state
        unchanged (no-op) so a bad id can never leave the scenario with zero active epochs.
        """
        with self._scenario_lock(scenario):
            target = self.load(scenario, epoch_id)
            if target is None:
                return
            current = self._active_for_locked(scenario)
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
        with self._scenario_lock(scenario):
            existing = self.load(scenario, epoch.epoch_id)
            if existing is not None:
                return existing
            state = "active" if self._active_for_locked(scenario) is None else "candidate"
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

        ``epoch_id`` is untrusted here and must be a sha256 hex digest; anything else is rejected
        (raising ``ValueError``) before any filesystem access, which the score-write helper degrades
        to a quarantine.
        """
        _require_epoch_id(epoch_id)
        with self._scenario_lock(scenario):
            existing = self.load(scenario, epoch_id)
            if existing is not None:
                return existing
            state = "active" if self._active_for_locked(scenario) is None else "candidate"
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
