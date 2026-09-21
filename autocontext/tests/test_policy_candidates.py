"""Trace-to-inactive-skill integration and boundary regressions (AC-1019)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autocontext.artifacts.policy_candidate import CandidateLimits, PolicyCandidateManifest, json_payload
from autocontext.context_bundles.models import BundleLifecycle, ContextBundle
from autocontext.context_bundles.store import ContextBundleStore
from autocontext.execution.isolated_python import IsolatedExecutionTimeout, local_isolation_available
from autocontext.execution.policy_candidate_data import (
    CandidateCase,
    SelectionExclusions,
    TrainingTrace,
    grid_observation,
    select_procedure,
    trace_from_grid_match,
)
from autocontext.execution.policy_candidate_runtime import invoke_candidate
from autocontext.execution.policy_candidates import inspect_grid_candidate, synthesize_grid_candidate
from autocontext.execution.policy_executor import PolicyExecutor
from autocontext.providers.base import CompletionResult, LLMProvider
from autocontext.scenarios.grid_ctf import GridCtfScenario

GOOD_SOURCE = """def propose_action(state: dict) -> dict:
    return {"status": "act", "action": {"aggression": 0.92, "defense": 0.48, "path_bias": 1.0}}
"""


class RecordingProvider(LLMProvider):
    def __init__(self, source: str = GOOD_SOURCE, stop_reason: str | None = None) -> None:
        self.source = source
        self.stop_reason = stop_reason
        self.calls: list[dict[str, Any]] = []

    def default_model(self) -> str:
        return "fixture-requested"

    def complete(self, **kwargs: Any) -> CompletionResult:
        self.calls.append(kwargs)
        return CompletionResult(
            text=self.source,
            model="fixture-served",
            usage={"input_tokens": 50, "output_tokens": 25},
            stop_reason=self.stop_reason,
        )


def trace(seed: int, *, success: bool = True, split: str = "train", group: str | None = None) -> TrainingTrace:
    row = {
        "score": 0.9 if success else 0.2,
        "had_illegal_actions": False,
        "errors": [],
        "moves_played": 1,
        "secret": "DO_NOT_COPY_SECRET",
        "final_answer": "DO_NOT_COPY_FINAL_ANSWER",
        "action": {"aggression": 0.923456789, "defense": 0.4, "path_bias": 1.0},
    }
    return trace_from_grid_match(row, seed=seed, trace_id=f"trace-{seed}", group_id=group or f"seed-{seed}", split=split)  # type: ignore[arg-type]


@pytest.fixture
def pool() -> list[TrainingTrace]:
    return [trace(0), trace(1), trace(2, success=False)]


@pytest.fixture
def cases() -> list[CandidateCase]:
    return [CandidateCase.grid(1001), CandidateCase.grid(1002), CandidateCase.grid(2001, shifted=True)]


def synth(tmp_path: Path, pool: list[TrainingTrace], cases: list[CandidateCase], **kwargs: Any):
    store = ContextBundleStore(tmp_path)
    if store.active_bundle("grid_ctf") is None:
        store.bootstrap(ContextBundle.create(scenario="grid_ctf", evaluator_epoch="baseline-verifier", components=[]))
    return synthesize_grid_candidate(
        store=ContextBundleStore(tmp_path),
        provider=kwargs.pop("provider", RecordingProvider()),
        provider_name="fixture",
        run_id="candidate-test",
        pool=pool,
        selected_ids=["trace-0", "trace-1"],
        counterexample_ids=["trace-2"],
        cases=cases,
        **kwargs,
    )


def require_isolation() -> None:
    if not local_isolation_available():
        pytest.skip("existing isolated policy boundary unavailable on this host")


def test_projection_excludes_raw_answers_and_secrets(pool):
    selected = select_procedure(pool, selected_ids=["trace-0", "trace-1"], counterexample_ids=["trace-2"])
    prompt = selected.prompt_context()
    assert "DO_NOT_COPY" not in prompt
    assert "0.923456789" not in prompt
    assert "trace-0" not in prompt
    assert "verify_outcome" in prompt
    assert selected.traces[-1].role == "training_counterexample"
    assert "DO_NOT_COPY" not in json_payload(selected)


@pytest.mark.parametrize("field,value", [("split", "heldout"), ("seed", 99), ("scenario", "other")])
def test_trace_provenance_cannot_be_relabelled(field, value):
    row = {"score": 0.9, "had_illegal_actions": False, "errors": [], "moves_played": 1, field: value}
    with pytest.raises(ValueError, match="provenance"):
        trace_from_grid_match(row, seed=0, trace_id="t", group_id="g", split="train")


def test_empty_store_fails_before_synthesis(tmp_path, pool, cases):
    provider = RecordingProvider()
    with pytest.raises(ValueError, match="existing baseline"):
        synthesize_grid_candidate(
            store=ContextBundleStore(tmp_path),
            provider=provider,
            provider_name="fixture",
            run_id="test",
            pool=pool,
            selected_ids=["trace-0", "trace-1"],
            cases=cases,
        )
    assert not provider.calls
    assert not list(tmp_path.rglob("active.json"))


def test_unsupported_training_contract_is_not_sent_to_provider(tmp_path, pool, cases):
    provider = RecordingProvider()
    pool[0] = pool[0].model_copy(update={"observation": grid_observation(0, shifted=True)})
    with pytest.raises(ValueError, match="unsupported contract"):
        synth(tmp_path, pool, cases, provider=provider)
    assert not provider.calls


@pytest.mark.parametrize("kind", ["too_few", "no_counterexample", "too_many", "caller_limits"])
def test_workflow_budget_and_evidence_requirements_before_call(tmp_path, pool, cases, kind):
    provider = RecordingProvider()
    kwargs = {}
    if kind == "too_few":
        cases = cases[1:]
    elif kind == "no_counterexample":
        cases = cases[:2]
    elif kind == "too_many":
        cases = [CandidateCase.grid(seed) for seed in range(1000, 1033)] + cases[-1:]
    else:
        kwargs["caller_limits"] = CandidateLimits(timeout_seconds=0.25)
    with pytest.raises(ValueError):
        synth(tmp_path, pool, cases, provider=provider, **kwargs)
    assert not provider.calls


def test_selected_training_budget_is_bounded():
    pool = [trace(seed) for seed in range(33)]
    with pytest.raises(ValueError, match="at most 32"):
        select_procedure(pool, selected_ids=[t.trace_id for t in pool])


@pytest.mark.parametrize(
    "exclusions",
    [
        SelectionExclusions(trace_ids=("trace-0",)),
        SelectionExclusions(group_ids=("seed-0",)),
        SelectionExclusions(seeds=(0,)),
    ],
)
def test_protected_selection_excluded(pool, exclusions):
    with pytest.raises(ValueError, match="protected"):
        select_procedure(pool, selected_ids=["trace-0", "trace-1"], exclusions=exclusions)


def test_holdout_group_and_near_duplicate_cannot_enter_selection(pool):
    pool.append(trace(77, split="heldout", group="seed-0"))
    with pytest.raises(ValueError, match="protected"):
        select_procedure(pool, selected_ids=["trace-0", "trace-1"])


def test_renamed_heldout_input_cannot_enter_selection(pool):
    heldout = trace(0, split="test", group="other-group").model_copy(update={"trace_id": "renamed-holdout"})
    with pytest.raises(ValueError, match="protected"):
        select_procedure([*pool, heldout], selected_ids=["trace-0", "trace-1"])


@pytest.mark.parametrize("selected", [["trace-0"], ["trace-0", "trace-0"], ["trace-0", "trace-2"]])
def test_insufficient_duplicate_or_failed_training_is_rejected(pool, selected):
    with pytest.raises(ValueError):
        select_procedure(pool, selected_ids=selected)


def test_synthesis_is_one_call_without_evaluation_inputs_and_never_activates(tmp_path, pool, cases):
    require_isolation()
    provider = RecordingProvider()
    result = synth(tmp_path, pool, cases, provider=provider)
    assert result.lifecycle == BundleLifecycle.PROPOSED
    assert all(e.passed for e in result.evaluations)
    assert [e.score is not None for e in result.evaluations] == [True, True, False]
    assert len(provider.calls) == 1
    assert "1001" not in provider.calls[0]["user_prompt"]
    assert json_payload(grid_observation(1001)) not in provider.calls[0]["user_prompt"]
    assert "DO_NOT_COPY" not in provider.calls[0]["user_prompt"]
    assert result.manifest.served_model == "fixture-served"
    assert result.manifest.requested_model == "fixture-requested"
    assert result.manifest.capability_grants == result.manifest.dependencies == ()
    store = ContextBundleStore(tmp_path)
    assert store.active_bundle("grid_ctf").components == ()
    inspected = inspect_grid_candidate(store, result.bundle_digest)
    assert inspected == result
    assert inspected.manifest.to_policy_artifact().source_code == inspected.manifest.skill.source
    assert not list(tmp_path.rglob("harness_state.json"))
    assert not list(tmp_path.rglob("*.py"))


def test_existing_active_bundle_unchanged(tmp_path, pool, cases):
    require_isolation()
    store = ContextBundleStore(tmp_path)
    baseline = ContextBundle.create(scenario="grid_ctf", evaluator_epoch="baseline-verifier", components=[])
    store.bootstrap(baseline)
    pointer = store.active_pointer("grid_ctf")
    result = synth(tmp_path, pool, cases)
    assert store.active_pointer("grid_ctf") == pointer
    assert store.load_bundle("grid_ctf", result.bundle_digest).parent_digest == baseline.digest


@pytest.mark.parametrize(
    "source",
    [
        "import os\n" + GOOD_SOURCE,
        'def propose_action(state):\n    return {"status":"act","action":{"aggression":2,"defense":0,"path_bias":1}}',
        'def propose_action(state):\n    return {"status":"act","action":{"aggression":0.9,"defense":0.9,"path_bias":1}}',
        'def propose_action(state):\n    return {"status":"abstain","reason":"policy_abstained"}',
        'def propose_action(state):\n    return {"status":"act","action":{"aggression":0.1,"defense":0.1,"path_bias":0.1}}',
        'def propose_action(state):\n    return state["seed"]',
    ],
)
def test_rejected_source_and_negative_evidence_are_retained(tmp_path, pool, cases, source):
    require_isolation()
    result = synth(tmp_path, pool, cases, provider=RecordingProvider(source))
    assert result.lifecycle == BundleLifecycle.REJECTED
    assert result.rejection_reasons
    assert source in result.manifest.skill.source
    assert inspect_grid_candidate(ContextBundleStore(tmp_path), result.bundle_digest) == result
    assert ContextBundleStore(tmp_path).active_bundle("grid_ctf").components == ()


def test_observation_lookup_memorization_fails_on_fresh_inputs(tmp_path, pool, cases):
    require_isolation()
    source = (
        "def propose_action(state):\n"
        f"    if state['enemy_spawn_bias'] == {pool[0].observation.enemy_spawn_bias!r}:\n"
        "        return {'status':'act','action':{'aggression':0.92,'defense':0.48,'path_bias':1.0}}\n"
        "    return {'status':'abstain','reason':'policy_abstained'}"
    )
    result = synth(tmp_path, pool, cases, provider=RecordingProvider(source))
    assert result.lifecycle == BundleLifecycle.REJECTED
    assert any(not e.passed for e in result.evaluations if e.lane == "fresh")


def test_truncated_synthesis_is_rejected_without_execution(tmp_path, pool, cases, monkeypatch):
    monkeypatch.setattr(PolicyExecutor, "execute_action", lambda *args: pytest.fail("must not execute truncated code"))
    result = synth(tmp_path, pool, cases, provider=RecordingProvider(stop_reason="max_tokens"))
    assert result.lifecycle == BundleLifecycle.REJECTED
    assert not result.evaluations


def test_unsupported_and_invalid_input_abstain_before_execution(tmp_path, pool, cases, monkeypatch):
    require_isolation()
    result = synth(tmp_path, pool, cases)
    monkeypatch.setattr(PolicyExecutor, "execute_action", lambda *args: pytest.fail("must abstain before execution"))
    for raw, reason in [
        (cases[-1].observation_json, "unsupported_contract"),
        ('{"seed":1}', "invalid_input"),
        (json_payload({**grid_observation(40).model_dump(), "secret": "blocked"}), "invalid_input"),
    ]:
        decision = invoke_candidate(result.manifest, raw, expected_digest=result.manifest.digest)
        assert decision.status == "abstain" and decision.reason == reason


@pytest.mark.parametrize(
    "field,value",
    [
        ("scenario_version", "changed"),
        ("input_schema", "{}"),
        ("evaluator_identity", "a" * 64),
        ("applicability", "{}"),
    ],
)
def test_incompatible_manifest_rejected_even_with_recomputed_digest(tmp_path, pool, cases, field, value, monkeypatch):
    require_isolation()
    result = synth(tmp_path, pool, cases)
    changed = PolicyCandidateManifest.model_validate({**result.manifest.model_dump(), field: value})
    monkeypatch.setattr(PolicyExecutor, "execute_action", lambda *args: pytest.fail("must reject before execution"))
    with pytest.raises(ValueError, match="incompatible"):
        invoke_candidate(changed, cases[0].observation_json, expected_digest=changed.digest)


def test_immutable_manifest_and_wrong_digest(tmp_path, pool, cases):
    require_isolation()
    result = synth(tmp_path, pool, cases)
    manifest = result.manifest
    digest = manifest.digest
    with pytest.raises(ValidationError):
        manifest.scenario = "other"
    manifest.skill.source = "tampered local copy"
    assert manifest.digest == digest
    with pytest.raises(ValueError, match="digest mismatch"):
        invoke_candidate(manifest, cases[0].observation_json, expected_digest="f" * 64)
    with pytest.raises(ValidationError, match="capability"):
        PolicyCandidateManifest.model_validate({**manifest.model_dump(), "capability_grants": ["network"]})
    with pytest.raises(ValueError, match="caller limits"):
        invoke_candidate(
            manifest, cases[0].observation_json, expected_digest=digest, caller_limits=CandidateLimits(timeout_seconds=0.5)
        )


@pytest.mark.parametrize("overlap", ["seed", "group", "input", "holdout"])
def test_evaluation_overlap_rejected_before_provider_call(tmp_path, pool, cases, overlap):
    provider = RecordingProvider()
    if overlap == "holdout":
        pool.append(trace(cases[0].seed, split="heldout"))
    else:
        updates = (
            {"seed": 0}
            if overlap == "seed"
            else {"group_id": "seed-0"}
            if overlap == "group"
            else {"observation_json": json_payload(pool[0].observation)}
        )
        cases[0] = cases[0].model_copy(update=updates)
    with pytest.raises(ValueError, match="overlaps"):
        synth(tmp_path, pool, cases, provider=provider)
    assert not provider.calls


def test_action_execution_is_bounded_and_schema_is_enforced():
    require_isolation()
    executor = PolicyExecutor(GridCtfScenario(), timeout_per_match=0.1)
    with pytest.raises(IsolatedExecutionTimeout):
        executor.execute_action("def choose_action(state):\n    while True:\n        pass", {})
    with pytest.raises(ValueError, match="import"):
        executor.execute_action("import os\ndef choose_action(state):\n    return {}", {})
    assert executor.execute_action("def choose_action(state):\n    return {'value': state['n'] + 1}", {"n": 3}) == {"value": 4}


def test_bundle_tampering_fails_inspection(tmp_path, pool, cases):
    require_isolation()
    result = synth(tmp_path, pool, cases)
    path = tmp_path / "grid_ctf" / "context_bundles" / "bundles" / f"{result.bundle_digest}.json"
    raw = json.loads(path.read_text())
    raw["components"][-1]["content"] = raw["components"][-1]["content"].replace("fixture-served", "tampered")
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="digest mismatch"):
        inspect_grid_candidate(ContextBundleStore(tmp_path), result.bundle_digest)
