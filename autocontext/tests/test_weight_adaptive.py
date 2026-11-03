from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.adaptive import (
    AdaptivePolicy,
    AdaptiveSearchError,
    run_adaptive_search,
    validate_equal_search_budgets,
)
from autocontext.weight_evolution.adaptive_provider import (
    AdaptiveFixtureInterrupted,
    EditProposal,
    FeedbackRoute,
    GenerationFeedback,
    GenerationView,
    OfflineFeedbackProvider,
    ProbeFixture,
    ProviderSessionAdapter,
)
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.session import SessionBlockedError, SessionIntegrityError, _read_record
from autocontext.weight_evolution.session_data import SessionDataBundle
from autocontext.weight_evolution.session_models import (
    CandidateState,
    Mechanism,
    ProbeOutput,
    SessionDecoding,
    SessionLimits,
    SessionSpec,
    SessionUsage,
)
from autocontext.weight_evolution.session_workflow import load_session_workflow

FIXTURE = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution" / "qwen_sessions" / "fixtures"
Scores = tuple[int, int, int]


def policy(rounds: int = 3) -> AdaptivePolicy:
    return AdaptivePolicy(
        proposal_rounds=rounds,
        proposal_reservation=SessionUsage(operations=1, editor_input_bytes=16384, editor_output_bytes=4096,
                                          simulated_seconds=1.0),
        edit_reservation=SessionUsage(operations=1, editor_input_bytes=8192, editor_output_bytes=4096,
                                      simulated_seconds=1.0),
        probe_reservation=SessionUsage(operations=1, editor_input_bytes=8192, editor_output_bytes=4096,
                                       probe_input_bytes=4096, probe_output_bytes=4096, simulated_seconds=1.0),
    )


def outputs(data: SessionDataBundle, scores: Scores) -> tuple[ProbeOutput, ...]:
    right = (r"\boxed{3}", r"\boxed{9}", "</think>green leaf")
    wrong = (r"\boxed{4}", r"\boxed{10}", "green leaf")
    return tuple(ProbeOutput(problem_id=case.problem.problem_id, text=right[index] if scores[index] else wrong[index])
                 for index, case in enumerate(data.practice))


def feedback(data: SessionDataBundle, scores: Scores) -> tuple[GenerationFeedback, ...]:
    return tuple(GenerationFeedback(problem_id=row.problem_id, score=score,
                                     response_sha256=hashlib.sha256(row.text.encode()).hexdigest())
                 for row, score in zip(outputs(data, scores), scores, strict=True))


def setup(mechanism: Mechanism = "C", *, trial_scores: tuple[Scores, ...] = ((1, 0, 1), (1, 1, 1), (0, 0, 1)),
          last_outcome: str = "return", all_proposals_fail: bool = False,
          baseline_outcome: str = "return") -> tuple[SessionSpec, AdaptivePolicy, SessionDataBundle, ProviderSessionAdapter]:
    frozen, data = load_session_workflow(FIXTURE)
    baseline_spec = next(program.spec for program in frozen.programs if program.spec.mechanism == mechanism)
    base = baseline_spec.baseline.state
    states: list[CandidateState] = []
    for index in range(len(trial_scores)):
        value = base.to_dict()
        if mechanism == "C":
            value["context_text"] = f"practice candidate {index + 1}"
        elif mechanism == "W":
            value["weight_descriptor"] = f"practice descriptor {index + 1}"
        else:
            value["decoding"] = SessionDecoding(temperature=0.1 + index * 0.2, top_p=0.95, top_k=20,
                                                presence_penalty=0.0).to_dict()
        states.append(CandidateState.model_validate_json(json.dumps(value)))
    base_scores: Scores = (0, 0, 1)
    probes = [ProbeFixture(state_digest=base.digest, outputs=outputs(data, base_scores)
                           if baseline_outcome == "return" else (), outcome=baseline_outcome)]  # type: ignore[arg-type]
    routes = []
    previous, previous_scores = base, base_scores
    ids = tuple(case.problem.problem_id for case in data.practice)
    for index, (state, scores) in enumerate(zip(states, trial_scores, strict=True)):
        proposal = EditProposal(mechanism=mechanism,
                                context_text=state.context_text if mechanism == "C" else None,
                                weight_descriptor=state.weight_descriptor if mechanism == "W" else None,
                                decoding=state.decoding if mechanism == "D" else None)
        view = GenerationView(mechanism=mechanism, state=previous, practice=data.practice_projection(ids),
                              feedback=feedback(data, previous_scores))
        outcome = "failure" if all_proposals_fail else last_outcome if index == len(states) - 1 else "return"
        routes.append(FeedbackRoute(view_digest=view.digest, response_text=json.dumps(proposal.to_dict()),
                                    outcome=outcome if outcome in ("failure", "interruption") else "return"))
        if all_proposals_fail:
            break
        probe_outcome = "failure" if outcome == "probe_failure" else "return"
        probes.append(ProbeFixture(state_digest=state.digest, outputs=outputs(data, scores)
                                   if probe_outcome == "return" else (), outcome=probe_outcome))
        previous, previous_scores = state, scores
    adapter = ProviderSessionAdapter(mechanism, OfflineFeedbackProvider(tuple(routes)), tuple(probes))
    search_policy = policy(len(trial_scores))
    limits = SessionLimits(**{f"max_{name}": value for name, value in search_policy.required_usage.to_dict().items()})
    spec = SessionSpec(session_id=f"adaptive-test-{mechanism.lower()}", mechanism=mechanism,
                       baseline=baseline_spec.baseline, data_digest=data.digest, scorer_digest=baseline_spec.scorer_digest,
                       adapter_digest=adapter.identity_digest, limits=limits)
    return spec, search_policy, data, adapter


def run(output: Path, values: tuple[SessionSpec, AdaptivePolicy, SessionDataBundle, ProviderSessionAdapter]) -> dict[str, Any]:
    spec, search_policy, data, adapter = values
    return run_adaptive_search(spec=spec, policy=search_policy, data=data, adapter=adapter, output_dir=output)


@pytest.mark.parametrize("mechanism", ["C", "W", "D"])
def test_earlier_winner_is_selected_restored_verified_and_sealed(tmp_path: Path, mechanism: Mechanism) -> None:
    values = setup(mechanism)
    result = run(tmp_path / "search", values)
    selection = _read_record(tmp_path / "search" / "selection.json")
    assert result["status"] == "sealed" and result["selected_round"] == 2
    assert result["restoration_performed"] is True
    assert result["selected_candidate_digest"] != result["candidate_digest"]
    assert selection["selected_state_digest"] == CandidateState.model_validate_json(
        json.dumps(result["candidate"]["state"]),
    ).digest
    assert selection["practice_scores"] == [1, 1, 1]
    assert [trial["correct"] for trial in selection["trials"]] == [2, 3, 1]
    assert result["reserved_usage"]["operations"] == 12
    assert result["artifact"]["source"]["declared_search_calls"] == 12
    assert result["selection_receipt_digest"] == canonical_digest(selection)
    assert result["confirmation_used"] is result["final_audit_used"] is result["promotion_eligible"] is False


def test_current_winner_does_not_charge_unnecessary_restoration(tmp_path: Path) -> None:
    values = setup(trial_scores=((1, 0, 0), (1, 0, 1), (1, 1, 1)))
    result = run(tmp_path / "search", values)
    assert result["selected_round"] == 3 and result["restoration_performed"] is False
    assert result["selected_candidate_digest"] == result["candidate_digest"]
    assert result["reserved_usage"]["operations"] == 11


def test_earliest_round_breaks_exact_practice_tie(tmp_path: Path) -> None:
    result = run(tmp_path / "search", setup(trial_scores=((1, 1, 1), (1, 1, 1), (0, 0, 1))))
    assert result["selected_round"] == 1


@pytest.mark.parametrize("outcome,operations,status", [("failure", 9, "proposal_failed"),
                                                       ("probe_failure", 12, "probe_failed")])
def test_failed_round_is_charged_and_excluded(tmp_path: Path, outcome: str, operations: int, status: str) -> None:
    output = tmp_path / "search"
    result = run(output, setup(last_outcome=outcome))
    assert result["selected_round"] == 2
    assert result["reserved_usage"]["operations"] == operations
    assert _read_record(output / "selection.json")["trials"][-1]["status"] == status
    assert len(list((output / "session" / "failures").iterdir())) == 1


def test_resume_reuses_every_result_without_provider_or_probe_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output, values = tmp_path / "search", setup()
    first = run(output, values)
    before = {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()}

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("resume dispatched")

    monkeypatch.setattr(ProviderSessionAdapter, "execute", forbidden)
    assert run(output, values) == first
    assert {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()} == before


def test_no_eligible_candidate_is_terminal_without_export(tmp_path: Path) -> None:
    output, values = tmp_path / "search", setup(all_proposals_fail=True)
    with pytest.raises(AdaptiveSearchError, match="no changed candidate"):
        run(output, values)
    assert _read_record(output / "failure.json")["reserved_usage"]["operations"] == 4
    assert not (output / "selection.json").exists()
    assert not (output / "session" / "export.json").exists()
    with pytest.raises(AdaptiveSearchError, match="no changed candidate"):
        run(output, values)
    assert len(list((output / "session" / "claims").iterdir())) == 4


def test_baseline_failure_stops_before_proposal(tmp_path: Path) -> None:
    output = tmp_path / "search"
    with pytest.raises(AdaptiveSearchError, match="baseline practice probe failed"):
        run(output, setup(baseline_outcome="failure"))
    assert len(list((output / "session" / "claims").iterdir())) == 1


def test_interrupted_proposal_blocks_restart_without_new_dispatch(tmp_path: Path) -> None:
    output, values = tmp_path / "search", setup(last_outcome="interruption")
    with pytest.raises(AdaptiveFixtureInterrupted):
        run(output, values)
    before = len(list((output / "session" / "claims").iterdir()))
    with pytest.raises(SessionBlockedError):
        run(output, values)
    assert len(list((output / "session" / "claims").iterdir())) == before == 8
    assert not (output / "selection.json").exists()


@pytest.mark.parametrize("counter", ["max_operations", "max_editor_input_bytes", "max_editor_output_bytes",
                                    "max_probe_input_bytes", "max_probe_output_bytes", "max_simulated_seconds"])
def test_worst_case_capacity_checked_before_output_creation(tmp_path: Path, counter: str) -> None:
    spec, search_policy, data, adapter = setup()
    limits = spec.limits.model_copy(update={counter: getattr(spec.limits, counter) - 1})
    spec = spec.model_copy(update={"limits": limits})
    with pytest.raises(ValueError, match="worst-case"):
        run(tmp_path / "search", (spec, search_policy, data, adapter))
    assert not (tmp_path / "search").exists()


def test_equal_budget_contract_is_shared_across_mechanisms() -> None:
    cases = [setup(mechanism) for mechanism in ("C", "W", "D")]
    specs = tuple(case[0] for case in cases)
    validate_equal_search_budgets(specs, cases[0][1])
    changed = specs[2].model_copy(update={"limits": specs[2].limits.model_copy(update={"max_operations": 13})})
    with pytest.raises(ValueError, match="equal limits"):
        validate_equal_search_budgets((*specs[:2], changed), cases[0][1])
    with pytest.raises(ValueError, match="ordered C/W/D"):
        validate_equal_search_budgets(tuple(reversed(specs)), cases[0][1])


@pytest.mark.parametrize("filename", ["manifest.json", "selection.json", "complete.json"])
def test_tampered_adaptive_record_rejected(tmp_path: Path, filename: str) -> None:
    output, values = tmp_path / "search", setup()
    run(output, values)
    path = output / filename
    envelope = json.loads(path.read_bytes())
    envelope["payload"]["schema_version"] = True
    envelope["digest"] = canonical_digest(envelope["payload"])
    path.write_text(json.dumps(envelope))
    with pytest.raises(SessionIntegrityError, match="different evidence"):
        run(output, values)


def test_selection_commitment_refuses_deleted_search_suffix(tmp_path: Path) -> None:
    output, values = tmp_path / "search", setup()
    run(output, values)
    # Model a removed suffix, including the seal. The prior selection commitment
    # must prevent an otherwise plausible shorter journal from dispatching again.
    (output / "session" / "export.json").unlink()
    for folder in ("claims", "results", "completed", "failures"):
        for path in (output / "session" / folder).iterdir():
            if int(path.stem) >= 8:
                path.unlink()
    with pytest.raises(SessionIntegrityError, match="missing its final session evidence"):
        run(output, values)
    assert len(list((output / "session" / "claims").iterdir())) == 7


@pytest.mark.parametrize("failed", [False, True])
def test_terminal_marker_prevents_reexecution_after_journal_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: bool,
) -> None:
    output, values = tmp_path / "search", setup(all_proposals_fail=failed)
    if failed:
        with pytest.raises(AdaptiveSearchError):
            run(output, values)
    else:
        run(output, values)
        (output / "session" / "export.json").unlink()
    sequence = max(int(path.stem) for path in (output / "session" / "claims").iterdir())
    for folder in ("claims", "results", "completed", "failures"):
        path = output / "session" / folder / f"{sequence:08d}.json"
        if path.exists():
            path.unlink()

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("terminal evidence was replaced by new dispatch")

    monkeypatch.setattr(ProviderSessionAdapter, "execute", forbidden)
    with pytest.raises(SessionIntegrityError, match="missing its final session evidence"):
        run(output, values)


@pytest.mark.parametrize("kind", ["malformed", "arbitrary", "symlink"])
def test_failure_marker_cannot_be_added_to_a_completed_search(tmp_path: Path, kind: str) -> None:
    output, values = tmp_path / "search", setup()
    run(output, values)
    failure = output / "failure.json"
    if kind == "malformed":
        failure.write_text("not json")
    elif kind == "arbitrary":
        failure.write_text(json.dumps({"payload": {}, "digest": canonical_digest({})}))
    else:
        failure.symlink_to(output / "complete.json")
    with pytest.raises(SessionIntegrityError, match="cannot coexist"):
        run(output, values)


def test_failure_marker_cannot_substitute_for_completion_on_a_sealed_search(tmp_path: Path) -> None:
    output, values = tmp_path / "search", setup()
    run(output, values)
    (output / "complete.json").rename(output / "failure.json")
    with pytest.raises(SessionIntegrityError, match="missing its final session evidence"):
        run(output, values)


def test_proposals_see_only_practice_and_verified_current_feedback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.weight_evolution.adaptive_provider import _prompt_view

    views = []
    complete = OfflineFeedbackProvider.complete

    def capture(self: OfflineFeedbackProvider, *args: Any, **kwargs: Any) -> Any:
        views.append(_prompt_view(kwargs["user_prompt"]).to_dict())
        return complete(self, *args, **kwargs)

    monkeypatch.setattr(OfflineFeedbackProvider, "complete", capture)
    run(tmp_path / "search", setup())
    assert len(views) == 3
    assert [[row["score"] for row in view["feedback"]] for view in views] == [[0, 0, 1], [1, 0, 1], [1, 1, 1]]
    encoded = json.dumps(views)
    for secret in ('"answer"', '"instructions"', "confirmation.", "audit."):
        assert secret not in encoded


def test_selection_is_durable_before_restore_and_final_verification(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "search"
    execute = ProviderSessionAdapter.execute
    phases = []

    def observe(self: ProviderSessionAdapter, request: Any) -> Any:
        phases.append(request.operation.operation_id)
        if request.operation.operation_id in ("restore-selected", "verify-selected"):
            assert (output / "selection.json").is_file()
        else:
            assert not (output / "selection.json").exists()
        return execute(self, request)

    monkeypatch.setattr(ProviderSessionAdapter, "execute", observe)
    run(output, setup())
    assert phases[-2:] == ["restore-selected", "verify-selected"]


def test_bad_paths_and_existing_unowned_files_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(SessionIntegrityError, match="symlinks"):
        run(link, setup())
    (real / "keep").write_text("unrelated")
    with pytest.raises(SessionIntegrityError, match="unexpected adaptive"):
        run(real, setup())
    assert (real / "keep").read_text() == "unrelated"
