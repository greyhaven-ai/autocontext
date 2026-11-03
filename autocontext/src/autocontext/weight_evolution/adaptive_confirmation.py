"""One-look, descriptive confirmation of already sealed adaptive fixture selections.

The search coordinator owns verification of its completed session journals and
publishes the all-arm selection commitment before calling this module. This
gate authenticates that retained commitment and the exact selected artifacts;
it never invokes a proposer or accepts a caller-supplied success boolean.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.comparison_evaluation import (
    FixtureEvidence,
    _validate_evidence,
    evaluate_comparison_fixture,
)
from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.comparison_models import ComparisonPlan
from autocontext.weight_evolution.models import Digest, FrozenModel
from autocontext.weight_evolution.session import _clear_staging, _immutable_json, _read_record
from autocontext.weight_evolution.session_data import SessionDataBundle
from autocontext.weight_evolution.session_models import CandidateRevision, Mechanism, SessionSpec, validate_candidate

MAX_SELECTION_BYTES = 4 * 1024 * 1024


class ConfirmationPolicy(FrozenModel):
    """Frozen descriptive thresholds; these do not assert statistical confidence."""

    schema_version: Literal[1] = 1
    minimum_mean_gain: float = Field(default=0.0, ge=0.0, le=1.0, allow_inf_nan=False)
    no_per_dataset_regression: Literal[True] = True
    max_looks: Literal[1] = 1
    statistical_significance_claimed: Literal[False] = False

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


class SelectedSearchArtifact(FrozenModel):
    mechanism: Mechanism
    candidate_digest: Digest
    artifact_digest: Digest
    selection_receipt_digest: Digest
    completion_digest: Digest
    completion_path: str = Field(min_length=1, max_length=1024)

    @field_validator("completion_path")
    @classmethod
    def relative_receipt(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (path.is_absolute() or any(part in {".", ".."} for part in path.parts)
                or str(path) != value or "\\" in value or path.name != "complete.json"):
            raise ValueError("search completion path must be a normalized relative complete.json path")
        return value


class ConfirmationSelection(FrozenModel):
    """The controller's pre-confirmation commitment to all three search winners."""

    schema_version: Literal[1] = 1
    execution_kind: Literal["fixture"] = "fixture"
    model_execution_performed: Literal[False] = False
    gpu_execution_deferred: Literal[True] = True
    data_digest: Digest
    search_completion_digest: Digest
    selections: tuple[SelectedSearchArtifact, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def complete_mechanisms(self) -> Self:
        if tuple(item.mechanism for item in self.selections) != ("C", "W", "D"):
            raise ValueError("confirmation selection requires ordered C/W/D commitments")
        if len({item.selection_receipt_digest for item in self.selections}) != 3:
            raise ValueError("each mechanism requires its own search selection receipt")
        if len({item.completion_path for item in self.selections}) != 3:
            raise ValueError("search completion paths must be distinct")
        if self.search_completion_digest != canonical_digest({
            item.mechanism: item.completion_digest for item in self.selections
        }):
            raise ValueError("aggregate search completion digest differs from per-arm commitments")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


def load_confirmation_selection(path: Path) -> ConfirmationSelection:
    value = load_small_json(path, max_bytes=MAX_SELECTION_BYTES)
    return ConfirmationSelection.model_validate_json(json.dumps(value, allow_nan=False))


def _verified_search_completion(path: Path) -> dict[str, Any]:
    """Verify linked terminal search receipts; full search replay belongs to its controller."""
    complete = _read_record(path)
    selected = _read_record(path.parent / "selection.json")
    exported = _read_record(path.parent / "session" / "export.json")
    manifest = _read_record(path.parent / "manifest.json")
    spec = SessionSpec.model_validate_json(json.dumps(manifest.get("spec"), allow_nan=False))
    if (complete.get("status") != "sealed" or complete.get("execution_kind") != "fixture"
            or complete.get("model_execution_performed") is not False
            or complete.get("gpu_execution_deferred") is not True or exported.get("status") != "sealed"):
        raise ValueError("confirmation requires completed fixture search and a sealed session export")
    if any(complete.get(key) is not False or selected.get(key) is not False
           for key in ("confirmation_used", "final_audit_used")):
        raise ValueError("search selection must precede any confirmation or final-audit use")
    if any(record.get("spec_digest") != spec.digest for record in (manifest, complete, selected, exported)):
        raise ValueError("search records disagree on the frozen session spec")
    if exported.get("data_digest") != spec.data_digest:
        raise ValueError("sealed export and session data identities differ")
    chosen = CandidateRevision.model_validate_json(json.dumps(selected.get("selected_candidate"), allow_nan=False))
    final = CandidateRevision.model_validate_json(json.dumps(complete.get("candidate"), allow_nan=False))
    validate_candidate(spec, chosen)
    validate_candidate(spec, final)
    if (chosen.digest != selected.get("selected_candidate_digest")
            or chosen.digest != complete.get("selected_candidate_digest")
            or chosen.state.digest != selected.get("selected_state_digest")
            or chosen.state.digest != complete.get("selected_state_digest")
            or chosen.state.digest != final.state.digest or final.digest != complete.get("candidate_digest")):
        raise ValueError("sealed candidate state differs from the pre-confirmation selected revision")
    if canonical_digest(selected) != complete.get("selection_receipt_digest"):
        raise ValueError("search selection receipt differs from its completion identity")
    for name in ("candidate_digest", "artifact_digest", "artifact", "history_digest", "reserved_usage", "observed_usage"):
        if name not in complete or canonical_digest(complete[name]) != canonical_digest(exported.get(name)):
            raise ValueError(f"search completion differs from sealed export: {name}")
    if canonical_digest(complete["artifact"]) != complete["artifact_digest"]:
        raise ValueError("selected artifact differs from its content identity")
    surface = {"C": "context_text", "W": "weight_descriptor", "D": "decoding"}[spec.mechanism]
    actual = complete["artifact"]
    exported_value = actual.get("text") if surface == "context_text" else actual.get("content")
    expected_value = getattr(final.state, surface)
    if surface == "decoding":
        expected_value = final.state.decoding.settings()
        exported_value = {key: actual.get(key) for key in expected_value}
    if canonical_digest(exported_value) != canonical_digest(expected_value):
        raise ValueError("exported artifact differs from the selected candidate surface")
    return {"complete": complete, "spec": spec}


def build_confirmation_selection(
    data: SessionDataBundle, *, completion_paths: tuple[Path, ...], selection_path: Path,
) -> ConfirmationSelection:
    """Construct the frozen all-arm commitment after the caller replays all searches."""
    if len(completion_paths) != 3:
        raise ValueError("confirmation requires three completed search paths in C/W/D order")
    entries = []
    for mechanism, path in zip(("C", "W", "D"), completion_paths, strict=True):
        verified = _verified_search_completion(path)
        complete, spec = verified["complete"], verified["spec"]
        if spec.mechanism != mechanism or spec.data_digest != data.digest:
            raise ValueError("search completion mechanism or data differs from confirmation selection")
        relative = path.absolute().relative_to(selection_path.absolute().parent).as_posix()
        entries.append(SelectedSearchArtifact.model_validate_json(json.dumps({
            "mechanism": mechanism, "candidate_digest": complete["candidate_digest"],
            "artifact_digest": complete["artifact_digest"],
            "selection_receipt_digest": complete["selection_receipt_digest"],
            "completion_digest": canonical_digest(complete), "completion_path": relative,
        })))
    return ConfirmationSelection(data_digest=data.digest, selections=tuple(entries),
                                 search_completion_digest=canonical_digest({
                                     entry.mechanism: entry.completion_digest for entry in entries
                                 }))


def _source_identity() -> dict[str, Any]:
    package = Path(__file__).parent
    names = ("adaptive_confirmation.py", "adaptive.py", "session.py", "session_data.py", "session_models.py",
             "comparison_models.py", "comparison_evaluation.py", "comparison_fixtures.py", "scorers.py",
             "artifacts.py", "models.py", "datasets.py")
    return {"name": "adaptive-fixture-confirmation-v1",
            "sources": [file_identity(package / name, max_bytes=1024 * 1024) for name in names]}


def _validate_selection(
    plan: ComparisonPlan, data: SessionDataBundle, selection: ConfirmationSelection, selection_path: Path,
) -> None:
    if selection.data_digest != data.digest:
        raise ValueError("selection and confirmation data identities differ")
    if plan.cases != data.confirmation or plan.development.cases != data.practice:
        raise ValueError("confirmation must use exactly the pinned confirmation role and practice provenance")
    if {case.dataset for case in data.confirmation} != {"math500", "gsm8k", "ifeval"}:
        raise ValueError("confirmation requires all three dataset routes")
    artifacts = (plan.artifacts.learned_context, plan.artifacts.edited_checkpoint, plan.artifacts.selected_decoding)
    if tuple(item.artifact_digest for item in selection.selections) != tuple(artifact.digest for artifact in artifacts):
        raise ValueError("confirmation artifacts differ from the sealed search selection")
    for binding in selection.selections:
        verified = _verified_search_completion(selection_path.parent / binding.completion_path)
        complete, spec = verified["complete"], verified["spec"]
        if spec.mechanism != binding.mechanism or spec.data_digest != data.digest:
            raise ValueError("search completion mechanism or data differs from confirmation selection")
        baseline = {name: getattr(plan.artifacts, name).to_dict()
                    for name in ("base_checkpoint", "base_context", "base_decoding")}
        if (canonical_digest(spec.baseline.to_dict()) != canonical_digest(baseline)
                or spec.scorer_digest != plan.protocol.scorer_digest):
            raise ValueError("confirmation baseline or scorer differs from the search contract")
        if (canonical_digest(complete) != binding.completion_digest
                or complete["candidate_digest"] != binding.candidate_digest
                or complete["artifact_digest"] != binding.artifact_digest
                or complete["selection_receipt_digest"] != binding.selection_receipt_digest):
            raise ValueError("search completion changed from its all-arm selection commitment")


def _gate(evaluation: dict[str, Any], policy: ConfirmationPolicy) -> list[dict[str, Any]]:
    metrics = {(row["condition_id"], row["dataset"]): row for row in evaluation["metrics"]}
    outcomes = []
    for mechanism in ("C", "W", "D"):
        effects: list[float] = []
        dataset_gains = {}
        for dataset in ("math500", "gsm8k", "ifeval"):
            baseline = metrics["A", dataset]["problem_scores"]
            candidate = metrics[mechanism, dataset]["problem_scores"]
            differences = [candidate[key] - baseline[key] for key in sorted(baseline)]
            effects.extend(differences)
            dataset_gains[dataset] = statistics.fmean(differences)
        gain = statistics.fmean(effects)
        nonregression = all(value >= 0.0 for value in dataset_gains.values())
        clears_threshold = gain >= policy.minimum_mean_gain
        outcomes.append({"mechanism": mechanism, "status": "confirmed" if nonregression and clears_threshold else "vetoed",
                         "mean_gain": gain, "n_problem_blocks": len(effects), "dataset_gains": dataset_gains,
                         "minimum_gain_met": clears_threshold, "no_dataset_regression": nonregression})
    return outcomes


def confirm_adaptive_selection(
    plan: ComparisonPlan, evidence: FixtureEvidence, *, data: SessionDataBundle,
    selection_path: Path, policy: ConfirmationPolicy, output_dir: Path,
) -> dict[str, Any]:
    """Freeze one confirmation look before grading; exact local replay is safe.

    Search must already have ended and the coordinator must have verified the
    sealed receipts named by ``selection_path``. This is a trusted local API,
    not an attestation of files independently supplied by an untrusted caller.
    An incomplete claim may finish deterministic grading only for the identical
    selection, evidence, policy and source closure. It cannot accept a fallback.
    """
    plan = ComparisonPlan.model_validate_json(json.dumps(plan.to_dict(), allow_nan=False))
    evidence = FixtureEvidence.model_validate_json(json.dumps(evidence.to_dict(), allow_nan=False))
    data = SessionDataBundle.model_validate_json(json.dumps(data.to_dict(), allow_nan=False))
    policy = ConfirmationPolicy.model_validate_json(json.dumps(policy.to_dict(), allow_nan=False))
    selection = load_confirmation_selection(selection_path)
    _validate_selection(plan, data, selection, selection_path)
    scorer = SyntheticComparisonScorer()
    if scorer.lock_digest != plan.protocol.scorer_digest:
        raise ValueError("confirmation scorer differs from its pinned identity")
    _validate_evidence(plan, evidence)
    root = output_dir.absolute()
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise ValueError("confirmation output must not traverse symlinks")
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".confirmation.lock"
    if lock.exists() or lock.is_symlink():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock):
        _clear_staging(root)
        existing = {path.name for path in root.iterdir()}
        allowed = {".confirmation.lock", "claim.json", "report.json", "complete.json"}
        if existing - allowed or ("claim.json" not in existing and existing - {".confirmation.lock"}):
            raise ValueError("confirmation directory contains unowned or unclaimed evidence")
        if "complete.json" in existing and "report.json" not in existing:
            raise ValueError("completed confirmation report is missing; preserve evidence for reconciliation")
        claim = {"schema_version": 1, "execution_kind": "fixture", "look": 1,
                 "selection": selection.to_dict(), "selection_digest": selection.digest,
                 "selection_file": file_identity(selection_path, max_bytes=MAX_SELECTION_BYTES),
                 "confirmation_split": data.split_commitments()["confirmation"],
                 "plan_digest": plan.digest, "evidence_digest": evidence.digest,
                 "policy": policy.to_dict(), "policy_digest": policy.digest, "controller": _source_identity()}
        _immutable_json(root / "claim.json", claim)
        evaluation = evaluate_comparison_fixture(plan, evidence, scorer).to_dict()
        outcomes = _gate(evaluation, policy)
        result = {"schema_version": 1, "execution_kind": "fixture", "model_execution_performed": False,
                  "gpu_execution_deferred": True, "promotion_eligible": False, "benchmark_result_claimed": False,
                  "statistical_significance_claimed": False, "look": 1,
                  "status": "confirmed" if all(row["status"] == "confirmed" for row in outcomes) else "vetoed",
                  "claim_digest": canonical_digest(claim), "selection_digest": selection.digest,
                  "search_completion_digest": selection.search_completion_digest,
                  "plan_digest": plan.digest, "evidence_digest": evidence.digest,
                  "policy": policy.to_dict(), "outcomes": outcomes,
                  "comparison_report_digest": canonical_digest(evaluation), "comparison": evaluation,
                  "scope": "one-look-descriptive-synthetic-fixture-gate-not-real-promotion"}
        _immutable_json(root / "report.json", result)
        complete = {"schema_version": 1, "claim_digest": canonical_digest(claim),
                    "report_digest": canonical_digest(result), "selection_digest": selection.digest,
                    "status": result["status"]}
        _immutable_json(root / "complete.json", complete)
        # Independently reread publication. The immutable helpers reject semantic
        # changes including integer/boolean substitutions and duplicate keys.
        if canonical_digest(_read_record(root / "complete.json")) != canonical_digest(complete):
            raise ValueError("confirmation completion changed after publication")
        return result
