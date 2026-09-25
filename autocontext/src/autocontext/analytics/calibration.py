"""Periodic human calibration and spot-check workflow (AC-260).

Defines a lightweight sampling workflow for human review of judge rubrics
and evolving playbooks. High-risk cases (large score jumps, near-perfect
scores, contradictory rubric satisfaction) are prioritized for review.
"""

from __future__ import annotations

import json
import math
import random
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autocontext.analytics.facets import RunFacet
from autocontext.context_bundles.models import stable_digest
from autocontext.util.file_lock import advisory_path_lock
from autocontext.util.json_io import read_json, write_json

if TYPE_CHECKING:
    from autocontext.storage.sqlite_store import SQLiteStore

# Score threshold for "near-perfect"
_PERFECT_THRESHOLD = 0.95


class CalibrationSample(BaseModel):
    """A run selected for human calibration review."""

    sample_id: str
    run_id: str
    scenario: str
    scenario_family: str = ""
    agent_provider: str = ""
    generation_index: int = 0
    risk_score: float = 0.0
    risk_reasons: list[str] = Field(default_factory=list)
    best_score: float = 0.0
    score_delta: float = 0.0
    playbook_mutation_size: int = 0
    evaluator_epoch: str | None = None
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationSample:
        return cls.model_validate(data)


class CalibrationOutcome(BaseModel):
    """Human calibration decision for a sample."""

    outcome_id: str
    sample_id: str
    decision: str = ""  # approve, reject, needs_adjustment
    reviewer: str = ""
    notes: str = ""
    rubric_quality: str = ""  # good, degraded, overfit, unstable
    playbook_quality: str = ""  # good, degraded, bloated, drifted
    recommended_action: str = "none"  # none, rollback_rubric, rollback_playbook, investigate
    created_at: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationOutcome:
        return cls.model_validate(data)


class CalibrationRound(BaseModel):
    """A periodic calibration round with samples and outcomes."""

    round_id: str
    created_at: str
    samples: list[CalibrationSample] = Field(default_factory=list)
    outcomes: list[CalibrationOutcome] = Field(default_factory=list)
    status: str = "pending"  # pending, in_progress, completed
    summary: str = ""
    mixed_epoch: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CalibrationRound:
        return cls.model_validate(data)


def compute_round_mixed_epoch(samples: list[CalibrationSample]) -> bool:
    """True when the samples span more than one evaluator class.

    None (unknown/legacy) is its own class comparable only with None, so it is
    NOT filtered out: a known epoch mixed with None spans two classes and is
    flagged, while an all-None or empty aggregate is not.
    """
    return len({s.evaluator_epoch for s in samples}) > 1


class SpotCheckSampler:
    """Selects high-risk cases for human calibration review."""

    def __init__(self, max_samples: int = 10) -> None:
        self._max_samples = max_samples

    def sample(
        self,
        facets: list[RunFacet],
        drift_warnings: list[Any] | None = None,
    ) -> list[CalibrationSample]:
        if not facets:
            return []

        now = datetime.now(UTC).isoformat()
        warnings = drift_warnings or []

        # Build set of (scenario, provider, release) combos flagged by warnings.
        # Release is part of the scope so the same provider/family in a different
        # release window does not get boosted accidentally.
        flagged: set[tuple[str, str, str]] = set()
        for w in warnings:
            for scenario in getattr(w, "affected_scenarios", []):
                for provider in getattr(w, "affected_providers", []):
                    releases = getattr(w, "affected_releases", []) or [""]
                    for release in releases:
                        flagged.add((scenario, provider, str(release)))

        scored: list[tuple[float, CalibrationSample]] = []
        for facet in facets:
            risk_score = 0.0
            risk_reasons: list[str] = []

            # Near-perfect score
            if facet.best_score >= _PERFECT_THRESHOLD:
                risk_score += 0.4
                risk_reasons.append("near_perfect")

            # Strong improvement signals (large score jumps)
            strong_jumps = sum(
                1 for d in facet.delight_signals
                if d.signal_type == "strong_improvement"
            )
            if strong_jumps > 0:
                risk_score += 0.3 * min(strong_jumps, 3)
                risk_reasons.append("large_score_jump")

            # Contradictory: has both friction and delight signals
            if facet.friction_signals and facet.delight_signals:
                risk_score += 0.2
                risk_reasons.append("contradictory_signals")

            # High rollback count
            if facet.rollbacks > 0:
                risk_score += 0.15
                risk_reasons.append("rollback_present")

            # Boost if this run's scenario+provider is flagged by warnings
            facet_release = str(facet.metadata.get("release", ""))
            if (
                (facet.scenario, facet.agent_provider, facet_release) in flagged
                or (facet.scenario, facet.agent_provider, "") in flagged
            ):
                risk_score += 0.3
                risk_reasons.append("drift_warning_match")

            # Score delta (approximate: best_score vs 0.5 baseline)
            score_delta = max(0.0, facet.best_score - 0.5)

            sample = CalibrationSample(
                sample_id=f"sample-{uuid.uuid4().hex[:8]}",
                run_id=facet.run_id,
                scenario=facet.scenario,
                scenario_family=facet.scenario_family,
                agent_provider=facet.agent_provider,
                generation_index=facet.total_generations - 1,
                risk_score=round(risk_score, 4),
                risk_reasons=risk_reasons,
                best_score=facet.best_score,
                score_delta=round(score_delta, 4),
                playbook_mutation_size=0,
                evaluator_epoch=facet.evaluator_epoch,
                created_at=now,
            )
            scored.append((risk_score, sample))

        # Sort by risk descending, take top N
        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[: self._max_samples]]


class CalibrationStore:
    """Persists calibration rounds and outcomes as JSON files."""

    def __init__(self, root: Path) -> None:
        self._rounds_dir = root / "calibration_rounds"
        self._outcomes_dir = root / "calibration_outcomes"
        self._rounds_dir.mkdir(parents=True, exist_ok=True)
        self._outcomes_dir.mkdir(parents=True, exist_ok=True)

    def persist_round(self, rnd: CalibrationRound) -> Path:
        path = self._rounds_dir / f"{rnd.round_id}.json"
        write_json(path, rnd.to_dict())
        return path

    def persist_acquisition_round(self, rnd: CalibrationRound) -> Path:
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", rnd.round_id) is None or "acquisition_policy" not in rnd.metadata:
            raise ValueError("acquisition round requires a safe id and frozen policy")
        with advisory_path_lock(self._rounds_dir / f"{rnd.round_id}.acquisition.lock"):
            path = self._rounds_dir / f"{rnd.round_id}.json"
            if path.exists():
                raise ValueError("acquisition round already exists; use resume")
            self.persist_round(rnd)
            return path

    def load_round(self, round_id: str) -> CalibrationRound | None:
        path = self._rounds_dir / f"{round_id}.json"
        if not path.exists():
            return None
        data = read_json(path)
        return CalibrationRound.from_dict(data)

    def list_rounds(self) -> list[CalibrationRound]:
        results: list[CalibrationRound] = []
        for path in sorted(self._rounds_dir.glob("*.json")):
            data = read_json(path)
            results.append(CalibrationRound.from_dict(data))
        return results

    def persist_outcome(self, outcome: CalibrationOutcome) -> Path:
        path = self._outcomes_dir / f"{outcome.outcome_id}.json"
        write_json(path, outcome.to_dict())
        return path

    def load_outcome(self, outcome_id: str) -> CalibrationOutcome | None:
        path = self._outcomes_dir / f"{outcome_id}.json"
        if not path.exists():
            return None
        data = read_json(path)
        return CalibrationOutcome.from_dict(data)

    def list_outcomes(self) -> list[CalibrationOutcome]:
        results: list[CalibrationOutcome] = []
        for path in sorted(self._outcomes_dir.glob("*.json")):
            data = read_json(path)
            results.append(CalibrationOutcome.from_dict(data))
        return results


class AcquisitionCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    case_id: str = Field(min_length=1, max_length=80)
    group_id: str = Field(min_length=1, max_length=80)
    scenario: str = Field(min_length=1)
    source_run_id: str = Field(min_length=1)
    source_trace_id: str = Field(min_length=1)
    source_trace_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    evaluator_identity: str = Field(pattern=r"^[a-f0-9]{64}$")
    task_prompt: str = Field(min_length=1, max_length=32768)
    output_text: str = Field(min_length=1, max_length=65536)
    rubric: str = Field(min_length=1, max_length=32768)
    judge_scores: tuple[float, ...] = Field(min_length=1)
    objective_score: float | None = None
    split: Literal["training", "development", "promotion_test"]
    generation_index: int = Field(default=0, ge=0, strict=True)
    criterion_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def bounded_scores(self) -> AcquisitionCandidate:
        if any(not 0 <= score <= 1 for score in self.judge_scores):
            raise ValueError("judge scores must be probabilities in [0, 1]")
        if self.objective_score is not None and not 0 <= self.objective_score <= 1:
            raise ValueError("objective score must be in [0, 1]")
        return self

    @property
    def content_digest(self) -> str:
        return stable_digest({"task_prompt": self.task_prompt, "output_text": self.output_text})

    @property
    def task_digest(self) -> str:
        return stable_digest(" ".join(self.task_prompt.casefold().split()))


class ProtectedMembership(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    group_ids: tuple[str, ...] = ()
    content_digests: tuple[str, ...] = ()
    task_digests: tuple[str, ...] = ()

    @model_validator(mode="after")
    def valid_membership(self) -> ProtectedMembership:
        if (any(not group for group in self.group_ids)
                or any(re.fullmatch(r"[a-f0-9]{64}", digest) is None
                       for digest in (*self.content_digests, *self.task_digests))):
            raise ValueError("protected memberships require group ids and SHA-256 content digests")
        return self

    @property
    def digest(self) -> str:
        return stable_digest({"group_ids": sorted(set(self.group_ids)),
                              "content_digests": sorted(set(self.content_digests)),
                              "task_digests": sorted(set(self.task_digests))})


class AcquisitionPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    max_labels: int = Field(default=10, ge=1, le=250, strict=True)
    random_audit_fraction: float = Field(default=0.2, ge=0, le=1)
    acceptance_boundary: float = Field(default=0.5, ge=0, le=1)
    seed: int = Field(default=1023, ge=0, strict=True)


def _acquisition_risk(candidate: AcquisitionCandidate, acceptance_boundary: float) -> tuple[float, list[str]]:
    mean = sum(candidate.judge_scores) / len(candidate.judge_scores)
    disagreement = max(candidate.judge_scores) - min(candidate.judge_scores)
    boundary = max(0.0, 1 - abs(mean - acceptance_boundary) / max(acceptance_boundary, 1 - acceptance_boundary))
    conflict = abs(mean - candidate.objective_score) if candidate.objective_score is not None else 0.0
    reasons = [name for name, value in (("judge_disagreement", disagreement), ("acceptance_boundary", boundary),
                                       ("objective_conflict", conflict)) if value >= 0.2]
    return round(.45 * disagreement + .25 * boundary + .3 * conflict, 6), reasons


def select_acquisition_round(
    candidates: list[AcquisitionCandidate], policy: AcquisitionPolicy, protected: ProtectedMembership, *, round_id: str,
) -> CalibrationRound:
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", round_id) is None:
        raise ValueError("acquisition round id must be a safe filename component")
    by_id = {case.case_id: case for case in candidates}
    if len(by_id) != len(candidates):
        raise ValueError("duplicate acquisition case identity")
    splits: dict[str, set[str]] = {}
    content_splits: dict[str, set[str]] = {}
    task_splits: dict[str, set[str]] = {}
    for case in candidates:
        splits.setdefault(case.group_id, set()).add(case.split)
        content_splits.setdefault(case.content_digest, set()).add(case.split)
        task_splits.setdefault(case.task_digest, set()).add(case.split)
        if case.split != "promotion_test" and (case.group_id in protected.group_ids
                                                or case.content_digest in protected.content_digests
                                                or case.task_digest in protected.task_digests):
            raise ValueError("acquisition pool overlaps a protected split")
    if any(len(values) > 1 for values in (*splits.values(), *content_splits.values(), *task_splits.values())):
        raise ValueError("duplicate content or group crosses acquisition splits")
    available = [case for case in candidates if case.split != "promotion_test"]
    def rank(case: AcquisitionCandidate) -> tuple[float, str]:
        return -_acquisition_risk(case, policy.acceptance_boundary)[0], case.case_id
    ranked = sorted(available, key=rank)
    by_group: dict[str, AcquisitionCandidate] = {}
    for case in ranked:
        by_group.setdefault(case.group_id, case)
    seen_tasks: set[str] = set()
    eligible = []
    for case in sorted(by_group.values(), key=rank):
        if case.task_digest not in seen_tasks:
            seen_tasks.add(case.task_digest)
            eligible.append(case)
    eligible.sort(key=lambda case: case.case_id)
    budget = min(policy.max_labels, len(eligible))
    audit_count = min(budget, math.ceil(budget * policy.random_audit_fraction))
    rng = random.Random(policy.seed)
    audits = rng.sample(eligible, k=audit_count)
    audit_ids = {case.case_id for case in audits}
    targeted = sorted((case for case in eligible if case.case_id not in audit_ids), key=rank)[:budget - audit_count]
    selections = [(case, "targeted") for case in targeted] + [(case, "random_audit") for case in sorted(
        audits, key=lambda case: case.case_id)]
    now = datetime.now(UTC).isoformat()
    samples = []
    for case, source in selections:
        risk, reasons = _acquisition_risk(case, policy.acceptance_boundary)
        samples.append(CalibrationSample(
            sample_id=f"acq-{stable_digest((round_id, case.case_id))[:24]}", run_id=case.source_run_id,
            scenario=case.scenario, generation_index=case.generation_index,
            risk_score=risk, risk_reasons=reasons, evaluator_epoch=case.evaluator_identity,
            created_at=now, metadata={"case_id": case.case_id, "group_id": case.group_id, "split": case.split,
                                      "content_digest": case.content_digest, "task_digest": case.task_digest,
                                      "source_trace_id": case.source_trace_id,
                                      "source_trace_digest": case.source_trace_digest, "task_prompt": case.task_prompt,
                                      "output_text": case.output_text, "rubric": case.rubric,
                                      "judge_scores": list(case.judge_scores), "objective_score": case.objective_score,
                                      "criterion_ids": list(case.criterion_ids), "acquisition_reason": source,
                                      "sampling_seed": policy.seed},
        ))
    return CalibrationRound(
        round_id=round_id, created_at=now, samples=samples, outcomes=[], status="pending",
        mixed_epoch=compute_round_mixed_epoch(samples),
        summary=f"{len(targeted)} targeted and {len(audits)} random-audit requests; no human labels assigned",
        metadata={"acquisition_policy": policy.model_dump(mode="json"),
                  "protected_membership": protected.model_dump(mode="json"),
                  "protected_membership_digest": protected.digest,
                  "pool_digest": stable_digest([case.model_dump(mode="json") for case in sorted(
                      candidates, key=lambda case: case.case_id)])},
    )


def _acquisition_round(store: CalibrationStore, round_id: str) -> CalibrationRound:
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", round_id) is None:
        raise ValueError("acquisition round id must be a safe filename component")
    rnd = store.load_round(round_id)
    if rnd is None or "acquisition_policy" not in rnd.metadata:
        raise ValueError("acquisition round does not exist")
    protected = ProtectedMembership.model_validate(rnd.metadata["protected_membership"])
    if protected.digest != rnd.metadata["protected_membership_digest"]:
        raise ValueError("protected split membership changed")
    return rnd


def _latest_outcome(rnd: CalibrationRound, sample_id: str) -> CalibrationOutcome | None:
    return next((outcome for outcome in reversed(rnd.outcomes) if outcome.sample_id == sample_id), None)


def _sync_acquisition_label(sqlite: SQLiteStore, rnd: CalibrationRound, sample: CalibrationSample) -> int | None:
    labeled = [outcome for outcome in rnd.outcomes if outcome.sample_id == sample.sample_id
               and outcome.decision in {"label", "correct"}]
    acquisition_id = stable_digest((rnd.round_id, sample.sample_id))
    existing = sqlite.get_human_feedback_by_acquisition_id(acquisition_id)
    if not labeled:
        if existing is not None:
            raise ValueError("feedback row exists without human-authored label provenance")
        return None
    latest = labeled[-1]
    score = latest.metadata["human_score"]
    correction = existing is not None and latest.decision == "correct"
    return sqlite.record_acquired_feedback(
        acquisition_id=acquisition_id, scenario_name=sample.scenario, agent_output=sample.metadata["output_text"],
        human_score=score, human_notes=latest.notes, reviewer=latest.reviewer,
        criterion_scores=latest.metadata["criterion_scores"], correction=correction,
    )


def record_acquisition_review(
    store: CalibrationStore, sqlite: SQLiteStore, round_id: str, sample_id: str, *, reviewer: str,
    decision: Literal["label", "skip", "correct", "disagree"], human_score: float | None = None,
    rationale: str = "", criterion_scores: dict[str, float] | None = None,
    protected: ProtectedMembership | None = None,
) -> CalibrationOutcome:
    if not reviewer.strip():
        raise ValueError("human reviewer identity is required")
    if decision not in {"label", "skip", "correct", "disagree"}:
        raise ValueError("unknown acquisition review decision")
    lock_path = store._rounds_dir / f"{round_id}.acquisition.lock"
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", round_id) is None:
        raise ValueError("acquisition round id must be a safe filename component")
    with advisory_path_lock(lock_path):
        rnd = _acquisition_round(store, round_id)
        sample = next((value for value in rnd.samples if value.sample_id == sample_id), None)
        if sample is None:
            raise ValueError("sample is not part of this acquisition round")
        frozen = ProtectedMembership.model_validate(rnd.metadata["protected_membership"])
        current = protected or frozen
        if (sample.metadata["split"] == "promotion_test"
                or sample.metadata["group_id"] in (*frozen.group_ids, *current.group_ids)
                or sample.metadata["content_digest"] in (*frozen.content_digests, *current.content_digests)
                or sample.metadata["task_digest"] in (*frozen.task_digests, *current.task_digests)):
            raise ValueError("protected examples cannot be labeled")
        if decision != "skip" and (not isinstance(human_score, (int, float)) or isinstance(human_score, bool)
                                   or not math.isfinite(human_score) or not 0 <= human_score <= 1):
            raise ValueError("a human-authored score in [0, 1] is required")
        if decision == "skip" and human_score is not None:
            raise ValueError("skipped examples cannot carry a human label")
        scores = criterion_scores or {}
        if any(key not in sample.metadata["criterion_ids"] or not isinstance(value, (int, float))
               or not math.isfinite(value) or not 0 <= value <= 1 for key, value in scores.items()):
            raise ValueError("criterion feedback must match the selected typed rubric")
        _sync_acquisition_label(sqlite, rnd, sample)
        previous = _latest_outcome(rnd, sample_id)
        if (previous is not None and previous.decision == decision and previous.reviewer == reviewer
                and previous.notes == rationale and previous.metadata.get("human_score") == human_score
                and previous.metadata.get("criterion_scores") == scores):
            return previous
        has_label = any(outcome.decision in {"label", "correct"} for outcome in rnd.outcomes
                        if outcome.sample_id == sample_id)
        if decision == "label" and has_label:
            raise ValueError("human label exists; use explicit correction or disagreement")
        if decision == "correct" and not has_label:
            raise ValueError("cannot correct a missing human label")
        if decision == "disagree" and (not has_label or previous is not None and previous.reviewer == reviewer):
            raise ValueError("a different reviewer must identify a disagreement with an existing label")
        if decision == "skip" and has_label:
            raise ValueError("labeled samples cannot be skipped")
        revision = sum(outcome.sample_id == sample_id for outcome in rnd.outcomes) + 1
        outcome = CalibrationOutcome(
            outcome_id=f"acq-{stable_digest((round_id, sample_id, revision))[:32]}", sample_id=sample_id,
            decision=decision, reviewer=reviewer, notes=rationale, created_at=datetime.now(UTC).isoformat(),
            metadata={"human_score": human_score, "criterion_scores": scores, "revision": revision,
                      "group_id": sample.metadata["group_id"], "content_digest": sample.metadata["content_digest"],
                      "acquisition_reason": sample.metadata["acquisition_reason"]},
        )
        rnd.outcomes.append(outcome)
        latest = {_sample.sample_id: _latest_outcome(rnd, _sample.sample_id) for _sample in rnd.samples}
        rnd.status = ("completed" if all(value is not None and value.decision in {"label", "correct", "skip"}
                                          for value in latest.values()) else "in_progress")
        store.persist_round(rnd)
        if decision in {"label", "correct"}:
            _sync_acquisition_label(sqlite, rnd, sample)
        return outcome


def reconcile_acquisition_round(store: CalibrationStore, sqlite: SQLiteStore, round_id: str) -> CalibrationRound:
    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", round_id) is None:
        raise ValueError("acquisition round id must be a safe filename component")
    with advisory_path_lock(store._rounds_dir / f"{round_id}.acquisition.lock"):
        rnd = _acquisition_round(store, round_id)
        for sample in rnd.samples:
            _sync_acquisition_label(sqlite, rnd, sample)
        return rnd


def pending_acquisition_samples(store: CalibrationStore, round_id: str) -> list[CalibrationSample]:
    rnd = _acquisition_round(store, round_id)
    return [sample for sample in rnd.samples if (latest := _latest_outcome(rnd, sample.sample_id)) is None
            or latest.decision == "disagree"]


def export_acquisition_labels(
    store: CalibrationStore, sqlite: SQLiteStore, round_id: str, *, protected: ProtectedMembership | None = None,
) -> dict[str, Any]:
    rnd = _acquisition_round(store, round_id)
    frozen = ProtectedMembership.model_validate(rnd.metadata["protected_membership"])
    current = protected or frozen
    exports: dict[str, Any] = {"training": [], "development": [], "summary": {
        "targeted_selected": sum(s.metadata["acquisition_reason"] == "targeted" for s in rnd.samples),
        "random_audit_selected": sum(s.metadata["acquisition_reason"] == "random_audit" for s in rnd.samples),
        "unresolved_disagreements": 0, "skipped": 0,
    }}
    groups: dict[str, str] = {}
    task_splits: dict[str, str] = {}
    for sample in rnd.samples:
        group = sample.metadata["group_id"]
        split = sample.metadata["split"]
        if (group in (*frozen.group_ids, *current.group_ids)
                or sample.metadata["content_digest"] in (*frozen.content_digests, *current.content_digests)
                or sample.metadata["task_digest"] in (*frozen.task_digests, *current.task_digests)):
            raise ValueError("acquisition export contains a protected example")
        if (group in groups and groups[group] != split
                or sample.metadata["task_digest"] in task_splits
                and task_splits[sample.metadata["task_digest"]] != split):
            raise ValueError("acquisition group or task crossed train/development split")
        groups[group] = split
        task_splits[sample.metadata["task_digest"]] = split
        latest = _latest_outcome(rnd, sample.sample_id)
        if latest is None:
            continue
        if latest.decision == "disagree":
            exports["summary"]["unresolved_disagreements"] += 1
            continue
        if latest.decision == "skip":
            exports["summary"]["skipped"] += 1
            continue
        if split not in {"training", "development"} or latest.decision not in {"label", "correct"}:
            raise ValueError("only reviewed training/development labels can be exported")
        acquisition_id = stable_digest((round_id, sample.sample_id))
        row = sqlite.get_human_feedback_by_acquisition_id(acquisition_id)
        if (row is None or row["human_score"] != latest.metadata["human_score"]
                or row["reviewer"] != latest.reviewer or row["human_notes"] != latest.notes
                or row["agent_output"] != sample.metadata["output_text"]
                or json.loads(row["criterion_scores_json"]) != latest.metadata["criterion_scores"]):
            raise ValueError("human feedback is missing or differs from the calibration outcome")
        exports[split].append({"sample_id": sample.sample_id, "group_id": group,
                               "content_digest": sample.metadata["content_digest"],
                               "task_digest": sample.metadata["task_digest"],
                               "task_prompt": sample.metadata["task_prompt"], "rubric": sample.metadata["rubric"],
                               "agent_output": row["agent_output"], "human_score": row["human_score"],
                               "human_notes": row["human_notes"], "reviewer": row["reviewer"],
                               "criterion_scores": latest.metadata["criterion_scores"],
                               "evaluator_identity": sample.evaluator_epoch,
                               "source_trace_id": sample.metadata["source_trace_id"],
                               "source_trace_digest": sample.metadata["source_trace_digest"],
                               "acquisition_reason": sample.metadata["acquisition_reason"]})
    return exports
