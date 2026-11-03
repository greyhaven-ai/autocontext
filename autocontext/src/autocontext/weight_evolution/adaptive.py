"""Practice-driven, bounded editing search over the durable M6 session ledger.

Proposal calls are inspection operations: their reservation is durable before
the provider runs. Selection uses complete practice probes only, then commits
before any budgeted restoration and sealing. Confirmation is a separate caller
phase; no held-out content or outcome enters this controller's decisions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.adaptive_provider import parse_proposal_receipt
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, regular_file
from autocontext.weight_evolution.models import FrozenModel
from autocontext.weight_evolution.session import (
    MAX_EXPORTED_SEARCH_UNITS,
    EditingSession,
    SessionAdapter,
    SessionIntegrityError,
    _clear_staging,
    _immutable_json,
    _read_record,
    _same,
    _search_units,
)
from autocontext.weight_evolution.session_data import SessionDataBundle
from autocontext.weight_evolution.session_models import (
    AdapterResult,
    CandidateRevision,
    EditOperation,
    InspectOperation,
    ProbeOperation,
    SessionOperation,
    SessionSpec,
    SessionUsage,
    apply_edit,
    initial_candidate,
)


class AdaptiveSearchError(ValueError):
    """A frozen search cannot produce a valid sealed candidate."""


class AdaptivePolicy(FrozenModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["fixture"] = "fixture"
    proposal_rounds: int = Field(gt=0, le=100)
    proposal_reservation: SessionUsage
    edit_reservation: SessionUsage
    probe_reservation: SessionUsage
    selection_rule: Literal["highest-practice-correct-then-earliest-round"] = (
        "highest-practice-correct-then-earliest-round"
    )

    @model_validator(mode="after")
    def valid_reservations(self) -> Self:
        # Reuse M6's typed operation reservation constraints, including one call
        # per operation, positive finite time, and probe/editor byte separation.
        InspectOperation(operation_id="policy-proposal", candidate_digest="0" * 64,
                         reservation=self.proposal_reservation)
        EditOperation(operation_id="policy-edit", candidate_digest="0" * 64,
                      reservation=self.edit_reservation, context_text="policy validation")
        ProbeOperation(operation_id="policy-probe", candidate_digest="0" * 64,
                       reservation=self.probe_reservation, problem_ids=("practice",))
        if _search_units(self.required_usage) > MAX_EXPORTED_SEARCH_UNITS:
            raise ValueError("worst-case adaptive budget exceeds the comparison artifact cost cap")
        return self

    @property
    def required_usage(self) -> SessionUsage:
        """Reserve baseline, every full trial, restoration, and final verification."""
        total = self.probe_reservation
        for _ in range(self.proposal_rounds):
            total = total.add(self.proposal_reservation).add(self.edit_reservation).add(self.probe_reservation)
        return total.add(self.edit_reservation).add(self.probe_reservation)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


def validate_equal_search_budgets(specs: tuple[SessionSpec, ...], policy: AdaptivePolicy) -> None:
    """Require comparable frozen C/W/D capacities before any session dispatch."""
    policy = AdaptivePolicy.model_validate_json(json.dumps(policy.to_dict()))
    specs = tuple(SessionSpec.model_validate_json(json.dumps(spec.to_dict())) for spec in specs)
    if tuple(spec.mechanism for spec in specs) != ("C", "W", "D"):
        raise ValueError("adaptive study requires exactly ordered C/W/D session specs")
    if len({spec.session_id for spec in specs}) != 3:
        raise ValueError("adaptive study session IDs must be distinct")
    first = specs[0]
    for spec in specs:
        if (spec.limits != first.limits or spec.baseline != first.baseline or spec.data_digest != first.data_digest
                or spec.scorer_digest != first.scorer_digest):
            raise ValueError("adaptive mechanisms require equal limits, baseline, data and scorer")
        if not policy.required_usage.within_limits(spec.limits):
            raise ValueError("adaptive limits cannot cover the declared worst-case search and restoration")


def adaptive_provenance() -> dict[str, Any]:
    package = Path(__file__).parent
    return {"controller": "practice-adaptive-fixture-search-v1", "execution_kind": "fixture",
            "model_execution_performed": False, "gpu_execution_deferred": True,
            "sources": [file_identity(package / name, max_bytes=1024 * 1024) for name in (
                "adaptive.py", "adaptive_provider.py", "session.py", "session_models.py", "session_data.py",
                "comparison_fixtures.py", "scorers.py", "artifacts.py", "models.py",
            )]}


def _safe_directory(root: Path) -> None:
    if any(path.is_symlink() for path in (root, *root.parents)):
        raise SessionIntegrityError("adaptive paths must not traverse symlinks")
    root.mkdir(parents=True, exist_ok=True)


def _verified_result(session: EditingSession, operation: SessionOperation, receipt: dict[str, Any]) -> AdapterResult:
    """Read message content only after the M6 controller independently replays it."""
    snapshot = session.snapshot()
    try:
        index = snapshot["operation_digests"].index(operation.digest)
    except ValueError as exc:
        raise SessionIntegrityError("proposal operation is absent from the verified journal") from exc
    saved_receipt = snapshot["operations"][index]
    _same(receipt, saved_receipt, "proposal receipt differs from the verified journal")
    if receipt["status"] != "completed":
        raise SessionIntegrityError("cannot read a result for a failed proposal")
    raw = _read_record(session.output_dir / "results" / f"{index + 1:08d}.json")
    result = AdapterResult.model_validate_json(json.dumps(raw))
    if result.digest != receipt["result_digest"] or result.operation_digest != operation.digest:
        raise SessionIntegrityError("proposal result differs from its verified receipt")
    return result


def _candidate(receipt: dict[str, Any]) -> CandidateRevision:
    return CandidateRevision.model_validate_json(json.dumps(receipt["candidate"]))


def _scores(receipt: dict[str, Any], practice_ids: tuple[str, ...]) -> tuple[int, ...]:
    if receipt["status"] != "completed":
        raise AdaptiveSearchError("practice evaluation did not complete")
    feedback = receipt["feedback"]
    if tuple(row["problem_id"] for row in feedback) != practice_ids:
        raise SessionIntegrityError("selection requires complete ordered practice coverage")
    if any(row["candidate_digest"] != receipt["candidate_digest"] for row in feedback):
        raise SessionIntegrityError("practice feedback belongs to a different candidate")
    return tuple(row["score"] for row in feedback)


def _terminal_failure(root: Path, phase: str, reason: str, session: EditingSession) -> None:
    snapshot = session.snapshot()
    _immutable_json(root / "failure.json", {"schema_version": 1, "status": "failed", "phase": phase,
                                           "reason": reason, "history_digest": snapshot["history_digest"],
                                           "reserved_usage": snapshot["reserved_usage"],
                                           "operation_digests": snapshot["operation_digests"],
                                           "model_execution_performed": False, "gpu_execution_deferred": True})
    raise AdaptiveSearchError(reason)


def run_adaptive_search(*, spec: SessionSpec, policy: AdaptivePolicy, data: SessionDataBundle,
                        adapter: SessionAdapter, output_dir: Path) -> dict[str, Any]:
    """Propose, probe and select using practice; return a verified sealed export.

An exact resume replays all prior results without redispatching completed work.
Unknown claims remain blocked by M6. Successful but invalid provider proposals
consume their inspection reservation and are excluded, never silently repaired.
"""
    spec = SessionSpec.model_validate_json(json.dumps(spec.to_dict()))
    policy = AdaptivePolicy.model_validate_json(json.dumps(policy.to_dict()))
    data = SessionDataBundle.model_validate_json(json.dumps(data.to_dict()))
    if not policy.required_usage.within_limits(spec.limits):
        raise ValueError("adaptive limits cannot cover the declared worst-case search and restoration")
    if data.digest != spec.data_digest or adapter.identity_digest != spec.adapter_digest:
        raise ValueError("adaptive data or adapter identity differs from the frozen spec")
    practice_ids = tuple(case.problem.problem_id for case in data.practice)
    root = output_dir.absolute()
    _safe_directory(root)
    lock = root / ".adaptive.lock"
    if lock.exists() or lock.is_symlink():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock):
        _clear_staging(root)
        allowed = {".adaptive.lock", "manifest.json", "session", "selection.json", "complete.json", "failure.json"}
        inventory = {path.name for path in root.iterdir()}
        if inventory - allowed or ("manifest.json" not in inventory and inventory - {".adaptive.lock"}):
            raise SessionIntegrityError("unexpected adaptive files or unowned nonempty output directory")
        if {"complete.json", "failure.json"} <= inventory:
            raise SessionIntegrityError("adaptive completion and failure markers cannot coexist")
        manifest = {"schema_version": 1, "spec": spec.to_dict(), "spec_digest": spec.digest,
                    "policy": policy.to_dict(), "policy_digest": policy.digest,
                    "split_commitments": data.split_commitments(), "controller": adaptive_provenance()}
        _immutable_json(root / "manifest.json", manifest)
        session = EditingSession(spec=spec, data=data, adapter=adapter, output_dir=root / "session")
        snapshot = session.snapshot()
        for marker, expected_status in (("complete.json", "sealed"), ("failure.json", "active")):
            path = root / marker
            if path.exists() or path.is_symlink():
                terminal = _read_record(path)
                if (snapshot["operation_digests"] != terminal.get("operation_digests")
                        or snapshot["history_digest"] != terminal.get("history_digest")
                        or snapshot["status"] != expected_status):
                    raise SessionIntegrityError("terminal adaptive receipt is missing its final session evidence")
        selection_path = root / "selection.json"
        if selection_path.exists() or selection_path.is_symlink():
            committed = _read_record(selection_path)
            committed_ops = committed.get("operation_digests")
            if not isinstance(committed_ops, list) or snapshot["operation_digests"][:len(committed_ops)] != committed_ops:
                raise SessionIntegrityError("committed selection is missing its prior search evidence")
        seen: list[str] = []

        def invoke(operation: SessionOperation) -> dict[str, Any]:
            prior = session.snapshot()["operation_digests"]
            if prior[:len(seen)] != seen or (len(prior) > len(seen) and prior[len(seen)] != operation.digest):
                raise SessionIntegrityError("session journal diverges from the deterministic adaptive program")
            receipt = session.run(operation)
            seen.append(operation.digest)
            return receipt

        current = initial_candidate(spec)
        baseline = invoke(ProbeOperation(operation_id="baseline-practice", candidate_digest=current.digest,
                                         reservation=policy.probe_reservation, problem_ids=practice_ids))
        if baseline["status"] != "completed":
            _terminal_failure(root, "baseline", "baseline practice probe failed", session)
        baseline_scores = _scores(baseline, practice_ids)
        last = baseline
        trials: list[dict[str, Any]] = []
        eligible: list[tuple[int, int, CandidateRevision, dict[str, Any]]] = []
        for round_index in range(1, policy.proposal_rounds + 1):
            propose = InspectOperation(operation_id=f"propose-{round_index}", candidate_digest=current.digest,
                                       reservation=policy.proposal_reservation, target="practice", problem_ids=practice_ids)
            last = invoke(propose)
            trial: dict[str, Any] = {"round": round_index, "proposal_operation_digest": propose.digest,
                                     "proposal_receipt_digest": canonical_digest(last)}
            if last["status"] != "completed":
                trials.append({**trial, "status": "proposal_failed"})
                continue
            result = _verified_result(session, propose, last)
            try:
                proposal = parse_proposal_receipt(result.message, mechanism=spec.mechanism)
                edit = proposal.proposal.to_edit(operation_id=f"edit-{round_index}", candidate_digest=current.digest,
                                                 reservation=policy.edit_reservation)
                apply_edit(spec, current, edit)
            except ValueError:
                trials.append({**trial, "status": "invalid_proposal"})
                continue
            trial["edit_operation_digest"] = edit.digest
            last = invoke(edit)
            trial["edit_receipt_digest"] = canonical_digest(last)
            if last["status"] != "completed":
                trials.append({**trial, "status": "edit_failed"})
                continue
            current = _candidate(last)
            probe = ProbeOperation(operation_id=f"probe-{round_index}", candidate_digest=current.digest,
                                   reservation=policy.probe_reservation, problem_ids=practice_ids)
            last = invoke(probe)
            trial.update({"probe_operation_digest": probe.digest, "probe_receipt_digest": canonical_digest(last),
                          "candidate_digest": current.digest, "state_digest": current.state.digest})
            if last["status"] != "completed":
                trials.append({**trial, "status": "probe_failed"})
                continue
            scores = _scores(last, practice_ids)
            if current.state.digest == spec.baseline.state.digest:
                trials.append({**trial, "status": "baseline_state", "practice_scores": list(scores)})
                continue
            trials.append({**trial, "status": "eligible", "practice_scores": list(scores), "correct": sum(scores)})
            eligible.append((sum(scores), round_index, current, last))
        if not eligible:
            _terminal_failure(root, "selection", "no changed candidate has a complete practice probe", session)
        correct, chosen_round, chosen, chosen_probe = min(eligible, key=lambda row: (-row[0], row[1]))
        selection = {"schema_version": 1, "execution_kind": "fixture", "model_execution_performed": False,
                     "gpu_execution_deferred": True, "spec_digest": spec.digest, "policy_digest": policy.digest,
                     "selection_rule": policy.selection_rule, "selected_round": chosen_round,
                     "selected_candidate": chosen.to_dict(), "selected_candidate_digest": chosen.digest,
                     "selected_state_digest": chosen.state.digest, "practice_ids": list(practice_ids),
                     "practice_scores": list(_scores(chosen_probe, practice_ids)), "practice_correct": correct,
                     "selected_probe_receipt_digest": canonical_digest(chosen_probe),
                     "baseline_practice_scores": list(baseline_scores), "trials": trials,
                     "operation_digests": list(seen), "history_digest": canonical_digest(last),
                     "reserved_usage": last["reserved_total"], "confirmation_used": False, "final_audit_used": False}
        _immutable_json(selection_path, selection)
        restored = current.state.digest != chosen.state.digest
        if restored:
            field = {"C": "context_text", "W": "weight_descriptor", "D": "decoding"}[spec.mechanism]
            restore_payload = {field: getattr(chosen.state, field)}
            restore = EditOperation(operation_id="restore-selected", candidate_digest=current.digest,
                                    reservation=policy.edit_reservation, **restore_payload)
            last = invoke(restore)
            if last["status"] != "completed":
                _terminal_failure(root, "restore", "selected candidate restoration failed", session)
            current = _candidate(last)
        verified = invoke(ProbeOperation(operation_id="verify-selected", candidate_digest=current.digest,
                                         reservation=policy.probe_reservation, problem_ids=practice_ids))
        if verified["status"] != "completed":
            _terminal_failure(root, "verify", "selected candidate verification probe failed", session)
        if current.state.digest != chosen.state.digest or _scores(verified, practice_ids) != _scores(chosen_probe, practice_ids):
            _terminal_failure(root, "verify", "selected candidate practice evidence changed on verification", session)
        snapshot = session.snapshot()
        if snapshot["operation_digests"] != seen:
            raise SessionIntegrityError("adaptive journal contains unplanned operations")
        artifact = session.export_candidate()
        final = session.snapshot()
        summary = {"schema_version": 1, "execution_kind": "fixture", "status": "sealed",
                   "model_execution_performed": False, "gpu_execution_deferred": True, "promotion_eligible": False,
                   "spec_digest": spec.digest, "policy_digest": policy.digest,
                   "selection_receipt_digest": canonical_digest(selection), "selected_round": chosen_round,
                   "selected_candidate_digest": chosen.digest, "selected_state_digest": chosen.state.digest,
                   "candidate_digest": final["candidate_digest"], "candidate": final["candidate"],
                   "restoration_performed": restored, "artifact": artifact.to_dict(), "artifact_digest": artifact.digest,
                   "history_digest": final["history_digest"], "reserved_usage": final["reserved_usage"],
                   "observed_usage": final["observed_usage"], "operation_digests": list(seen),
                   "confirmation_used": False, "final_audit_used": False}
        _immutable_json(root / "complete.json", summary)
        return summary
