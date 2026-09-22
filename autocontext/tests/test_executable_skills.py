"""Explicit enrollment, replay, promotion gate and a real non-game Docker pilot."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time

import pytest
from pydantic import ValidationError

from autocontext.artifacts.policy_candidate import CandidateLimits, json_payload
from autocontext.context_bundles import BundleLifecycle, MatchedTrial, TrialLane
from autocontext.context_bundles.assembly import bundle_tool_context
from autocontext.context_bundles.models import BundleComponent, ComponentKind, ContextBundle
from autocontext.context_bundles.store import ContextBundleStore
from autocontext.execution.docker_skill import DockerSkillExecutor, DockerSkillResult
from autocontext.execution.executable_skills import (
    COMPONENT_KEY,
    DEFAULT_LIMITS,
    SCENARIO,
    ExecutableSkillManifest,
    SkillSourceEvidence,
    evaluator_identity,
    inspect_executable_skill,
    invoke_executable_skill,
    propose_schema_migration,
)
from autocontext.knowledge.harness_entries import SkillReference
from autocontext.runtime_images import PINNED_PYTHON_RUNTIME_IMAGE

SOURCE = '''def choose_action(state):
    return {"schema_version": 2, "display_name": state["name"],
            "status": "enabled" if state["enabled"] else "disabled"}
'''
INPUT = '{"schema_version":1,"name":"Ada","enabled":true}'
OUTPUT = '{"schema_version":2,"display_name":"Ada","status":"enabled"}'


def evidence():
    return (
        SkillSourceEvidence.create("example-1", "example", {"input": json.loads(INPUT), "output": json.loads(OUTPUT)}),
        SkillSourceEvidence.create("near-miss-1", "counterexample", {"input": {"schema_version": 3}, "abstain": True}),
    )


@pytest.fixture
def candidate(tmp_path):
    store = ContextBundleStore(tmp_path)
    store.bootstrap(ContextBundle.create(scenario=SCENARIO, evaluator_epoch=evaluator_identity(), components=[]))
    bundle = propose_schema_migration(store, SkillReference(entrypoint="choose_action", source=SOURCE),
                                     source_evidence=evidence(), run_id="test")
    return store, bundle


@pytest.fixture
def recording_executor(monkeypatch):
    calls = []

    def execute(self, source, input_json, limits, *, cancel=None):
        calls.append((source, input_json, limits))
        return DockerSkillResult(OUTPUT, elapsed_seconds=0.125, image_identity="fixture-image")

    monkeypatch.setattr(DockerSkillExecutor, "execute", execute)
    return calls


def invoke(candidate, input_json=INPUT, **kwargs):
    store, bundle = candidate
    return invoke_executable_skill(store, bundle.digest, input_json, mode=kwargs.pop("mode", "evaluation"), **kwargs)


def enroll_changed(candidate, mutate):
    store, bundle = candidate
    data = json.loads(bundle.components[0].content)
    mutate(data)
    changed = ContextBundle.create(scenario=SCENARIO, evaluator_epoch=bundle.evaluator_epoch,
                                   parent_digest=bundle.parent_digest, components=[
        BundleComponent.json(ComponentKind.TOOL_SPEC, COMPONENT_KEY, data),
    ])
    store.propose(changed, source_run_id="changed", source_generation=0)
    return store, changed


def promote(candidate):
    """Synthetic lifecycle evidence solely to test the existing serving gate."""
    store, bundle = candidate
    trials = [MatchedTrial(candidate_digest=bundle.digest, incumbent_digest=bundle.parent_digest,
                          evaluator_epoch=bundle.evaluator_epoch, cohort="test", fixture=f"{lane}-{i}",
                          fixture_digest=f"test-{lane}-{i}", seed=i, lane=lane,
                          candidate_score=0.9, incumbent_score=0.5)
              for lane, count in ((TrialLane.SCREEN, 2), (TrialLane.CONFIRMATION, 6), (TrialLane.HELDOUT, 2))
              for i in range(count)]
    store.record_matched_trials(SCENARIO, bundle.digest, trials)
    return store.promote(SCENARIO, bundle.digest, cohort="test", rationale="test fixture only")


def test_explicit_enrollment_replays_without_activation(candidate, recording_executor):
    store, bundle = candidate
    before = store.active_pointer(SCENARIO)
    manifest = inspect_executable_skill(store, bundle.digest)
    decision = invoke(candidate)
    assert decision.status == "success"
    assert json.loads(decision.output_json) == json.loads(OUTPUT)
    assert decision.artifact_digest == manifest.digest
    assert decision.source_sha256 == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert decision.input_sha256 == hashlib.sha256(INPUT.encode()).hexdigest()
    assert decision.environment_digest and decision.evaluator_identity == evaluator_identity()
    assert decision.model_calls == 0 and decision.execution_seconds == 0.125
    assert decision.image_identity == "fixture-image"
    assert store.candidate(SCENARIO, bundle.digest).lifecycle == BundleLifecycle.PROPOSED
    assert store.active_pointer(SCENARIO) == before
    assert len(recording_executor) == 1
    changed_skill = manifest.skill
    changed_skill.source = "unsafe mutation"
    assert manifest.skill.source == SOURCE


def test_reference_only_helpers_are_never_callable(candidate, recording_executor):
    store, bundle = candidate
    helper = ContextBundle.create(scenario=SCENARIO, evaluator_epoch=bundle.evaluator_epoch,
                                  parent_digest=bundle.parent_digest, components=[
        BundleComponent.json(ComponentKind.TOOL_SPEC, COMPONENT_KEY, {"description": "reference", "code": SOURCE}),
    ])
    store.propose(helper, source_run_id="reference", source_generation=0)
    rendered = bundle_tool_context(helper)
    assert "Reference helper (not installed or callable)" in rendered and SOURCE.strip() in rendered
    assert invoke((store, helper)).status == "execution_failure"
    assert not recording_executor


@pytest.mark.parametrize("value,reason", [
    ('{"schema_version":2,"name":"Ada","enabled":true}', "unsupported_schema_version"),
    ('{"schema_version":1,"name":"Ada","enabled":"true"}', "invalid_input"),
    ('{"schema_version":true,"name":"Ada","enabled":true}', "invalid_input"),
    ('{"schema_version":1,"name":"Ada","enabled":true,"extra":0}', "invalid_input"),
    ('{"schema_version":1,"name":"Ada","enabled":true,"enabled":false}', "invalid_input"),
    ('{"schema_version":1,"name":"\\ud800","enabled":true}', "invalid_input"),
    ('{"schema_version":NaN}', "invalid_input"),
    ("[" * 2000, "invalid_input"),
    ("x" * 65537, "input_limit"),
])
def test_unsupported_inputs_abstain_without_execution(candidate, recording_executor, value, reason):
    result = invoke(candidate, value)
    assert (result.status, result.reason) == ("abstention", reason)
    assert not recording_executor


@pytest.mark.parametrize("output,reason", [
    ("not-json", "invalid_output"),
    ('{"schema_version":2,"display_name":"Grace","status":"enabled"}', "migration_postcondition_failed"),
    ('{"schema_version":2,"display_name":"Ada","status":"enabled","extra":0}', "invalid_output"),
    ('{"schema_version":2,"display_name":"Ada","status":"enabled","status":"disabled"}', "invalid_output"),
    ('{"schema_version":2,"display_name":"Ada","status":"abstain"}', "invalid_output"),
])
def test_malformed_and_wrong_outputs_are_not_accepted(candidate, monkeypatch, output, reason):
    monkeypatch.setattr(DockerSkillExecutor, "execute", lambda *a, **k: DockerSkillResult(output))
    result = invoke(candidate)
    assert (result.status, result.reason, result.output_json) == ("verification_failure", reason, None)


@pytest.mark.parametrize("field,value", [
    ("dependencies", ["requests==1"]), ("capability_grants", ["network"]),
    ("runtime_image", "python:latest"), ("runtime", "local-python"),
    ("scenario_version", "other"), ("input_schema", "{}"), ("output_schema", "{}"),
    ("applicability", "{}"), ("evaluator_identity", "0" * 64),
    ("limits", {"timeout_seconds": 31}), ("limits", {"max_memory_mb": 32}),
    ("skill_json", '{"entrypoint":"other","source":"pass"}'),
])
def test_manifest_mismatches_fail_closed(candidate, recording_executor, field, value):
    def mutate(record):
        manifest = json.loads(record["manifest_json"])
        manifest[field] = value
        record["manifest_json"] = json_payload(manifest)
        # Rebind the claimed digest as an attacker creating a different artifact
        # could; compatibility checks must still reject unsupported contracts.
        from autocontext.context_bundles.models import stable_digest
        record["manifest_digest"] = stable_digest(manifest)
    changed = enroll_changed(candidate, mutate)
    assert invoke(changed).status == "execution_failure"
    assert not recording_executor


@pytest.mark.parametrize("mutation", ["eligibility", "implicit", "numeric", "kind", "digest", "source", "evidence"])
def test_artifact_eligibility_and_identity_drift(candidate, recording_executor, mutation):
    def mutate(record):
        if mutation == "eligibility":
            record["eligible"] = False
        elif mutation == "implicit":
            del record["eligible"]
            del record["kind"]
        elif mutation == "numeric":
            record["eligible"] = 1
        elif mutation == "kind":
            record["kind"] = "reference_helper"
        elif mutation == "digest":
            record["manifest_digest"] = "0" * 64
        else:
            manifest = json.loads(record["manifest_json"])
            if mutation == "source":
                skill = json.loads(manifest["skill_json"])
                skill["source"] += "\nraise RuntimeError('drift')"
                manifest["skill_json"] = json_payload(skill)
            else:
                manifest["source_evidence"][0]["content_sha256"] = "0" * 64
            record["manifest_json"] = json_payload(manifest)
    assert invoke(enroll_changed(candidate, mutate)).status == "execution_failure"
    assert not recording_executor


def test_caller_budget_and_preflight_cancellation(candidate, recording_executor):
    assert invoke(candidate, caller_limits=CandidateLimits(timeout_seconds=1)).status == "execution_failure"
    cancel = threading.Event()
    cancel.set()
    assert invoke(candidate, cancel=cancel).reason == "cancelled"
    assert invoke(candidate, executor=DockerSkillExecutor(image="python:latest")).reason == "runtime dependency mismatch"
    assert not recording_executor


def test_sandbox_absence_never_falls_back(candidate):
    result = invoke(candidate, executor=DockerSkillExecutor(docker_binary="/does/not/exist/docker"))
    assert (result.status, result.reason) == ("execution_failure", "sandbox_unavailable")


def test_serving_requires_durable_promotion_and_uses_identical_contract(candidate, recording_executor, tmp_path):
    store, bundle = candidate
    assert invoke(candidate, mode="serving").status == "execution_failure"
    assert not recording_executor
    promotion = promote(candidate)
    evaluated, served = invoke(candidate), invoke(candidate, mode="serving")
    assert evaluated.status == served.status == "success"
    assert evaluated.output_json == served.output_json
    assert evaluated.environment_digest == served.environment_digest
    assert recording_executor[0] == recording_executor[1]
    path = tmp_path / SCENARIO / "context_bundles" / "promotions" / f"{promotion.promotion_id}.json"
    path.unlink()
    assert invoke(candidate, mode="serving").status == "execution_failure"
    assert len(recording_executor) == 2


def test_bootstrap_does_not_substitute_for_a_promotion(candidate, recording_executor, tmp_path):
    _, bundle = candidate
    other = ContextBundleStore(tmp_path / "other")
    initial = ContextBundle.create(scenario=SCENARIO, evaluator_epoch=bundle.evaluator_epoch, components=bundle.components)
    other.bootstrap(initial)
    result = invoke((other, initial), mode="serving")
    assert result.status == "execution_failure" and "promotion" in result.reason
    assert not recording_executor


def test_revoked_promotion_during_execution_discards_result(candidate, monkeypatch):
    store, _ = candidate
    promote(candidate)
    def execute(*args, **kwargs):
        store.rollback(SCENARIO, rationale="test revocation")
        return DockerSkillResult(OUTPUT)
    monkeypatch.setattr(DockerSkillExecutor, "execute", execute)
    result = invoke(candidate, mode="serving")
    assert result.status == "execution_failure" and result.output_json is None


def test_manifest_immutability_and_source_evidence(candidate):
    manifest = inspect_executable_skill(candidate[0], candidate[1].digest)
    with pytest.raises(ValidationError):
        manifest.runtime_image = "python:latest"
    with pytest.raises(ValidationError):
        ExecutableSkillManifest.model_validate({**manifest.model_dump(), "source_evidence": [evidence()[0]]})
    with pytest.raises(ValueError, match="non-finite"):
        SkillSourceEvidence.create("invalid", "example", {"bad": float("inf")})


def test_artifact_drift_during_execution_discards_output(candidate, monkeypatch, tmp_path):
    _, bundle = candidate
    def execute(*args, **kwargs):
        path = tmp_path / SCENARIO / "context_bundles" / "bundles" / f"{bundle.digest}.json"
        data = json.loads(path.read_text())
        data["components"][0]["content"] += " "
        path.write_text(json.dumps(data))
        return DockerSkillResult(OUTPUT)
    monkeypatch.setattr(DockerSkillExecutor, "execute", execute)
    decision = invoke(candidate)
    assert decision.status == "execution_failure" and decision.output_json is None


def test_enrollment_requires_existing_compatible_baseline(tmp_path):
    store = ContextBundleStore(tmp_path)
    with pytest.raises(ValueError, match="existing baseline"):
        propose_schema_migration(store, SkillReference(entrypoint="choose_action", source=SOURCE),
                                 source_evidence=evidence(), run_id="test")
    assert store.active_pointer(SCENARIO) is None
    baseline = ContextBundle.create(scenario=SCENARIO, evaluator_epoch="old-epoch", components=[])
    store.bootstrap(baseline)
    with pytest.raises(ValueError, match="epoch"):
        propose_schema_migration(store, SkillReference(entrypoint="choose_action", source=SOURCE),
                                 source_evidence=evidence(), run_id="test")


@pytest.mark.skipif(os.environ.get("AUTOCONTEXT_RUN_DOCKER_TESTS") != "1", reason="requires explicit real Docker test lane")
def test_real_docker_executable_skill_boundary(candidate, tmp_path, monkeypatch):
    # This test is selected explicitly in CI's sandbox-integration job. Fixtures
    # contain no model-generated evidence and make no performance/promotion claim.
    subprocess.run(["docker", "pull", PINNED_PYTHON_RUNTIME_IMAGE], check=True, capture_output=True, timeout=120)
    result = invoke(candidate)
    assert result.status == "success", result
    assert result.model_calls == 0 and result.execution_seconds > 0 and result.image_identity
    assert invoke(candidate, INPUT.replace("true", "false")).output_json == json_payload({
        "schema_version": 2, "display_name": "Ada", "status": "disabled",
    })
    sentinel = tmp_path / "host-secret.txt"
    sentinel.write_text("host-secret")
    monkeypatch.setenv("AUTOCONTEXT_BRIDGE_TEST_SECRET", "must-not-cross")
    probe = f'''import os, pathlib, socket
assert 'AUTOCONTEXT_BRIDGE_TEST_SECRET' not in os.environ
assert not pathlib.Path({str(sentinel)!r}).exists()
for path in ['/input/skill.py', '/etc/bridge-probe']:
    try:
        pathlib.Path(path).write_text('host write')
    except OSError:
        pass
    else:
        raise AssertionError('filesystem write allowed')
try:
    socket.create_connection(('1.1.1.1', 53), timeout=0.2)
except OSError:
    pass
else:
    raise AssertionError('network allowed')
'''
    executor = DockerSkillExecutor()
    protected = executor.execute(probe + SOURCE, INPUT, DEFAULT_LIMITS)
    assert protected.failure is None, protected
    assert json.loads(protected.output) == json.loads(OUTPUT)
    assert sentinel.read_text() == "host-secret"
    for source, expected in [
        ("def choose_action(state):\n    raise RuntimeError('failed')", "candidate_error"),
        ("memory = bytearray(512 * 1024 * 1024)", "candidate_error"),
        ("while True: print('x' * 4096, flush=True)", "output_limit"),
        ("while True: pass", "timeout"),
    ]:
        limits = CandidateLimits(timeout_seconds=2, max_memory_mb=128, max_output_bytes=1024)
        outcome = executor.execute(source, INPUT, limits)
        assert outcome.failure == expected, outcome
    # Wait until this container is running before cancelling, so this exercises
    # mid-execution termination rather than just the preflight event check.
    cancel = threading.Event()
    results = []
    thread = threading.Thread(target=lambda: results.append(executor.execute(
        "while True: pass", INPUT, replace_limits(timeout_seconds=20), cancel=cancel,
    )))
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            running = subprocess.run(["docker", "ps", "-q", "--filter", "label=autocontext.skill=schema-migration-v1"],
                                     check=True, capture_output=True, timeout=5)
            if running.stdout.strip():
                break
            time.sleep(0.05)
        else:
            pytest.fail("cancellation fixture never reached a running container")
        cancel.set()
    finally:
        cancel.set()
        thread.join(timeout=15)
    assert not thread.is_alive() and results[0].failure == "cancelled", results
    leftover = subprocess.run(["docker", "ps", "-aq", "--filter", "label=autocontext.skill=schema-migration-v1"],
                              check=True, capture_output=True, timeout=5)
    assert not leftover.stdout.strip()


def replace_limits(**kwargs):
    return CandidateLimits.model_validate({**DEFAULT_LIMITS.model_dump(), **kwargs})
