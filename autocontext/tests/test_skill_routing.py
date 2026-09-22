"""Applicability, shared budgets, durable decisions and real provider cancellation."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.context_bundles import MatchedTrial, TrialLane
from autocontext.context_bundles.models import ContextBundle
from autocontext.context_bundles.store import ContextBundleStore
from autocontext.execution import skill_routing
from autocontext.execution.docker_skill import DockerSkillExecutor, DockerSkillResult
from autocontext.execution.executable_skills import (
    CONTRACT,
    SCENARIO,
    SkillSourceEvidence,
    evaluator_identity,
    propose_schema_migration,
)
from autocontext.execution.routed_model import ModelCallError, complete_routed_model
from autocontext.execution.skill_routing import route_schema_migration, routing_evaluator_identity
from autocontext.execution.skill_routing_models import ModelTarget, RoutingBudget, SkillRoutingConfig
from autocontext.knowledge.harness_entries import SkillReference
from autocontext.providers.base import CompletionResult
from autocontext.runtimes._workspace_process import default_shell_env, run_bounded_process
from autocontext.training.model_registry import DistilledModelRecord, ModelRegistry

INPUT = '{"schema_version":1,"name":"Ada","enabled":true}'
OUTPUT = '{"schema_version":2,"display_name":"Ada","status":"enabled"}'
SOURCE = '''def choose_action(state):
    return {"schema_version":2,"display_name":state["name"],"status":"enabled" if state["enabled"] else "disabled"}
'''


def target(model="fixture-model", **kwargs):
    return ModelTarget(provider="openai-compatible", model=model, api_key_env="ROUTE_API_KEY",
                       input_cost_per_1k=0.001, output_cost_per_1k=0.002, **kwargs)


def receipt(model="fixture-model", text=OUTPUT, **kwargs):
    data = dict(text=text, model=model, served_model=model, usage={"input_tokens": 100, "output_tokens": 30})
    return CompletionResult(**{**data, **kwargs})


@pytest.fixture
def pilot(tmp_path):
    store = ContextBundleStore(tmp_path / "knowledge")
    store.bootstrap(ContextBundle.create(scenario=SCENARIO, evaluator_epoch=evaluator_identity(), components=[]))
    bundle = propose_schema_migration(
        store, SkillReference(entrypoint="choose_action", source=SOURCE), run_id="routing-test",
        source_evidence=(SkillSourceEvidence.create("example", "example", {"input": json.loads(INPUT)}),
                         SkillSourceEvidence.create("shifted", "counterexample", {"schema_version": 99})),
    )
    registry = ModelRegistry(tmp_path / "models")
    return store, registry, bundle, tmp_path


def run(pilot, value=INPUT, *, config=None, **kwargs):
    store, registry, bundle, path = pilot
    return route_schema_migration(store, registry, value, config=config or SkillRoutingConfig(
        enabled=True, bundle_digest=bundle.digest), trace_root=path / "traces", mode=kwargs.pop("mode", "evaluation"), **kwargs)


def register(pilot, model_target):
    record = DistilledModelRecord(
        artifact_id="model-artifact", scenario=SCENARIO, scenario_family="pure-transformation",
        backend=model_target.provider, checkpoint_path=model_target.model, runtime_types=["provider"],
        activation_state="active", training_metrics={}, provenance={"source": "test"},
        metadata={"skill_routing": {"contract": CONTRACT, "target_digest": model_target.digest,
                                    "evaluator_identity": routing_evaluator_identity()}},
    )
    pilot[1].register(record)
    return record


def promote(pilot):
    store, _, bundle, _ = pilot
    trials = [MatchedTrial(candidate_digest=bundle.digest, incumbent_digest=bundle.parent_digest,
                          evaluator_epoch=bundle.evaluator_epoch, cohort="test", fixture=f"{lane}-{i}",
                          fixture_digest=f"test-{lane}-{i}", seed=i, lane=lane,
                          candidate_score=0.9, incumbent_score=0.5)
              for lane, count in ((TrialLane.SCREEN, 2), (TrialLane.CONFIRMATION, 6), (TrialLane.HELDOUT, 2))
              for i in range(count)]
    store.record_matched_trials(SCENARIO, bundle.digest, trials)
    store.promote(SCENARIO, bundle.digest, cohort="test", rationale="synthetic serving gate fixture")


@pytest.fixture
def skill(monkeypatch):
    calls = []

    def execute(*args, **kwargs):
        calls.append(args)
        return DockerSkillResult(OUTPUT, image_identity="fixture-image")

    monkeypatch.setattr(DockerSkillExecutor, "execute", execute)
    return calls


@pytest.fixture
def model(monkeypatch):
    calls = []

    def complete(model_target, input_json, **kwargs):
        calls.append((model_target, input_json, kwargs))
        return receipt(model_target.model)

    monkeypatch.setattr(skill_routing, "complete_routed_model", complete)
    return calls


def test_skill_first_and_durable_record(pilot, skill, model):
    result = run(pilot)
    assert result.status == "success" and result.selected_route == "skill"
    assert result.model_calls == 0 and len(skill) == 1 and not model
    assert result.input_sha256 == hashlib.sha256(INPUT.encode()).hexdigest()
    assert json.loads(result.output_json) == json.loads(OUTPUT)
    assert json.loads(Path(result.trace_path).read_text()) == result.model_dump(mode="json")


def test_opt_in_disabled_has_no_dispatch(pilot, skill, model):
    result = run(pilot, config=SkillRoutingConfig())
    assert (result.status, result.reason) == ("abstention", "routing_disabled")
    assert not skill and not model


def test_serving_requires_promotion_and_preserves_evaluation_contract(pilot, skill, model):
    blocked = run(pilot, mode="serving")
    assert blocked.status == "abstention" and not skill
    assert "not active" in blocked.attempts[0].reason
    promote(pilot)
    served, evaluated = run(pilot, mode="serving"), run(pilot)
    assert served.output_json == evaluated.output_json
    assert served.evaluator_identity == evaluated.evaluator_identity
    assert len(skill) == 2 and not model


def test_invalid_skill_falls_back_to_registered_model(pilot, monkeypatch, model):
    specific = target()
    register(pilot, specific)
    monkeypatch.setattr(DockerSkillExecutor, "execute", lambda *a, **k: DockerSkillResult(OUTPUT.replace("Ada", "Wrong")))
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, bundle_digest=pilot[2].digest,
                                                 specialized=specific, general=target("general-model")))
    assert result.selected_route == "specialized" and len(model) == 1
    assert result.attempts[0].reason == "migration_postcondition_failed"
    assert result.accounted_tokens == 130 and result.accounted_model_cost_usd == 0.00016
    assert not result.model_cost_complete  # configured prices, not a provider invoice


@pytest.mark.parametrize("change", ["absent", "deactivated", "contract", "endpoint", "epoch", "runtime"])
def test_stale_or_missing_specialized_model_uses_general(pilot, skill, model, change):
    specific = target()
    if change != "absent":
        record = register(pilot, specific)
        if change == "deactivated":
            record.activation_state = "disabled"
        elif change == "runtime":
            record.runtime_types = ["judge"]
        else:
            key = {"contract": "contract", "endpoint": "target_digest", "epoch": "evaluator_identity"}[change]
            record.metadata["skill_routing"][key] = "stale"
        pilot[1].register(record)
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True,
                                                 specialized=specific, general=target("general-model")))
    assert result.selected_route == "general" and len(model) == 1 and not skill
    assert result.attempts[1].status == "skipped"


@pytest.mark.parametrize("value", [INPUT.replace(":1,", ":99,"), INPUT.replace("true", '"true"'),
                                  INPUT[:-1] + ',"extra":0}', '{"name":"Ada"}'])
def test_shifted_inputs_cannot_become_verified_by_model_confidence(pilot, skill, model, value):
    specific = target()
    register(pilot, specific)
    result = run(pilot, value, config=SkillRoutingConfig(enabled=True, allow_network=True, bundle_digest=pilot[2].digest,
                                                        specialized=specific, general=target("general-model")))
    assert result.status == "abstention" and result.output_json is None
    assert not skill and len(model) == 1 and model[0][0].model == "general-model"
    assert result.attempts[-1].reason == "unsupported_input_proposal"


def test_model_abstention_is_explicit(pilot, monkeypatch):
    monkeypatch.setattr(skill_routing, "complete_routed_model", lambda *a, **k: receipt(text='{"abstain":true}'))
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, general=target()))
    assert result.reason == "no_verified_route" and result.attempts[-1].reason == "model_abstained"


def test_registry_backend_can_use_an_explicit_compatible_endpoint(pilot, model):
    specific = target()
    record = register(pilot, specific)
    record.backend = "mlx"
    record.checkpoint_path = "/operator/models/profile-checkpoint"
    pilot[1].register(record)
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True,
                                                 specialized=specific, specialized_backend="mlx"))
    assert result.selected_route == "specialized" and len(model) == 1
    assert result.attempts[-1].registered_artifact_id == record.artifact_id


def test_registry_revocation_between_resolution_and_dispatch(pilot, model, monkeypatch):
    specific = target()
    record = register(pilot, specific)
    original = skill_routing.resolve_provider_for_context

    def revoke(*args, **kwargs):
        decision = original(*args, **kwargs)
        pilot[1].deactivate(record.artifact_id)
        return decision

    monkeypatch.setattr(skill_routing, "resolve_provider_for_context", revoke)
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, specialized=specific))
    assert result.status == "abstention" and not model
    assert result.attempts[1].reason == "missing_or_incompatible_registered_model"


@pytest.mark.parametrize("reason", ["model_cleanup_unverified", "invalid_provider_receipt"])
def test_unverified_worker_receipt_or_cleanup_prevents_fallback(pilot, monkeypatch, reason):
    specific = target()
    register(pilot, specific)
    calls = []

    def complete(*args, **kwargs):
        calls.append(1)
        raise ModelCallError(reason)

    monkeypatch.setattr(skill_routing, "complete_routed_model", complete)
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True,
                                                 specialized=specific, general=target("general")))
    assert result.reason == reason and len(calls) == 1


def test_unreaped_worker_never_returns_accepted_output(tmp_path, monkeypatch):
    from autocontext.runtimes import _workspace_process
    original = _workspace_process._reap_process

    def unreaped(process):
        original(process)
        return -1

    monkeypatch.setattr(_workspace_process, "_reap_process", unreaped)
    result = run_bounded_process([sys.executable, "-c", "print('success')"], cwd=tmp_path,
                                 env=default_shell_env(), shell=False, timeout_ms=5000)
    assert result.exit_code == 126 and not result.stdout


def test_manual_override_bypasses_other_routes_and_never_escalates(pilot, skill, monkeypatch):
    calls = []

    def unavailable(selected, *args, **kwargs):
        calls.append(selected.model)
        raise ModelCallError("provider_unavailable")

    monkeypatch.setattr(skill_routing, "complete_routed_model", unavailable)
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, bundle_digest=pilot[2].digest,
                                                 specialized=target(), general=target("general"), override=target("manual")))
    assert calls == ["manual"] and not skill and result.output_json is None
    assert result.attempts[-1].accounting == "reservation"


@pytest.mark.parametrize("offline,network,endpoint,expected", [
    (False, False, None, "network_not_granted"), (True, True, None, "offline_endpoint_unavailable"),
    (True, True, "http://127.0.0.1:9999/v1", "verified_proposal"),
])
def test_offline_and_network_grants(pilot, model, monkeypatch, offline, network, endpoint, expected):
    monkeypatch.setenv("AUTOCONTEXT_OFFLINE", "1" if offline else "0")
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=network, general=target(base_url=endpoint)))
    assert result.attempts[-1].reason == expected
    assert len(model) == (expected == "verified_proposal")


def test_failed_calls_reserve_budget_across_fallback(pilot, monkeypatch):
    specific, general = target(), target("general")
    register(pilot, specific)
    calls = []

    def complete(selected, *args, **kwargs):
        calls.append(selected.model)
        raise ModelCallError("provider_unavailable")

    monkeypatch.setattr(skill_routing, "complete_routed_model", complete)
    budget = RoutingBudget(max_tokens=specific.max_input_tokens + specific.max_output_tokens)
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, specialized=specific,
                                                 general=general, budget=budget))
    assert calls == [specific.model]
    assert result.accounted_tokens == budget.max_tokens
    assert result.attempts[-1].reason == "token_budget_exhausted"


@pytest.mark.parametrize("budget,reason", [(RoutingBudget(max_attempts=0), "attempt_budget_exhausted"),
                                         (RoutingBudget(max_model_cost_usd=0.0), "cost_budget_exhausted"),
                                         (RoutingBudget(max_tokens=0), "token_budget_exhausted")])
def test_exhausted_budget_prevents_dispatch(pilot, model, budget, reason):
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, general=target(), budget=budget))
    assert not model and (result.reason == reason or result.attempts[-1].reason == reason)


@pytest.mark.parametrize("kwargs", [{"usage": {}}, {"usage": {"input_tokens": True, "output_tokens": 1}},
    {"usage": {"input_tokens": 3, "prompt_tokens": 4, "output_tokens": 1}},
    {"usage": {"input_tokens": 100000, "output_tokens": 1}}, {"cost_usd": float("nan")}, {"cost_usd": 100.0}])
def test_unverifiable_accounting_stops_all_fallback(pilot, monkeypatch, kwargs):
    specific = target()
    register(pilot, specific)
    calls = []

    def complete(*args, **kw):
        calls.append(1)
        return receipt(**kwargs)

    monkeypatch.setattr(skill_routing, "complete_routed_model", complete)
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, specialized=specific,
                                                 general=target("general")))
    assert result.reason == "provider_accounting_unverified" and len(calls) == 1
    assert result.output_json is None and result.accounted_tokens == 9216


@pytest.mark.parametrize("served", [None, "unexpected-model"])
def test_actual_served_model_must_match_pin(pilot, monkeypatch, served):
    monkeypatch.setattr(skill_routing, "complete_routed_model", lambda *a, **k: receipt(served_model=served))
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, general=target()))
    assert result.reason == "reported_model_mismatch" and result.output_json is None
    assert result.attempts[-1].accounting == "reservation"


def test_same_model_is_not_called_twice(pilot, monkeypatch):
    specific = target()
    register(pilot, specific)
    calls = []

    def invalid(*args, **kwargs):
        calls.append(1)
        return receipt(text="not JSON")

    monkeypatch.setattr(skill_routing, "complete_routed_model", invalid)
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, specialized=specific, general=specific))
    assert len(calls) == 1 and result.attempts[-1].reason == "duplicate_model_route"


def test_registered_model_revocation_discards_output(pilot, monkeypatch):
    specific = target()
    register(pilot, specific)

    def revoke(*args, **kwargs):
        pilot[1].deactivate("model-artifact")
        return receipt()

    monkeypatch.setattr(skill_routing, "complete_routed_model", revoke)
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, specialized=specific))
    assert result.output_json is None and result.attempts[1].reason == "model_registry_changed"


@pytest.mark.parametrize("when", ["before", "dispatch_record", "after_model", "final_record"])
def test_cancellation_including_persistence_boundaries(pilot, monkeypatch, when):
    cancel = threading.Event()
    calls = []
    original_write = skill_routing.write_json

    def write(path, data):
        original_write(path, data)
        if (when == "dispatch_record" and data["reason"] == "model_dispatch") or (
            when == "final_record" and data["status"] == "success"
        ):
            cancel.set()

    def complete(*args, **kwargs):
        calls.append(1)
        if when == "after_model":
            cancel.set()
        return receipt()

    monkeypatch.setattr(skill_routing, "write_json", write)
    monkeypatch.setattr(skill_routing, "complete_routed_model", complete)
    if when == "before":
        cancel.set()
    result = run(pilot, cancel=cancel, config=SkillRoutingConfig(enabled=True, allow_network=True, general=target()))
    assert result.reason == "cancelled" and result.output_json is None
    assert len(calls) == (when in {"after_model", "final_record"})


def test_deadline_is_not_reset_by_fallback(pilot, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    specific = target(timeout_seconds=10.0)
    register(pilot, specific)
    timeouts = []

    def complete(selected, *args, **kwargs):
        timeouts.append(kwargs["timeout_seconds"])
        now[0] += 8
        if len(timeouts) == 1:
            return receipt(selected.model, text="wrong")
        return receipt(selected.model)

    monkeypatch.setattr(skill_routing, "complete_routed_model", complete)
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, specialized=specific,
                                                 general=target("general"), budget=RoutingBudget(wall_seconds=12.0)))
    assert timeouts == [10.0, 4.0]
    assert result.reason == "request_timeout" and result.output_json is None


def test_oversized_input_never_encoded_or_dispatched(pilot, skill, model):
    class Oversized(str):
        def encode(self, *args, **kwargs):
            pytest.fail("oversized input encoded")
    result = run(pilot, Oversized("x" * 65537))
    assert result.reason == "input_limit" and result.input_json is None and result.input_sha256 is None
    assert not skill and not model


def test_trace_failure_prevents_dispatch(pilot, skill, model, monkeypatch):
    monkeypatch.setattr(skill_routing, "write_json", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        run(pilot)
    assert not skill and not model


@pytest.mark.parametrize("kwargs", [{"base_url": "https://secret@example.com/v1"},
                                   {"base_url": "https://example.com/v1?token=secret"},
                                   {"input_cost_per_1k": float("nan")}, {"api_key_env": "ANTHROPIC_BASE_URL"}])
def test_invalid_transport_and_price_configuration(kwargs):
    values = {**target().model_dump(), **kwargs}
    with pytest.raises(ValidationError):
        ModelTarget.model_validate(values)


def test_sub_microdollar_prices_cannot_bypass_zero_spend_budget(pilot, model):
    tiny = ModelTarget.model_validate({**target().model_dump(), "input_cost_per_1k": 1e-10, "output_cost_per_1k": 1e-10})
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, general=tiny,
                                                 budget=RoutingBudget(max_model_cost_usd=0.0)))
    assert result.attempts[-1].reason == "cost_budget_exhausted" and not model


@pytest.fixture
def endpoint():
    requests = []
    received = threading.Event()
    release = threading.Event()
    behavior = {"status": 200, "wait": False, "served_model": None,
                "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            received.set()
            if behavior["wait"]:
                release.wait(15)
            body = json.dumps({"id": "fixture", "object": "chat.completion", "created": 1,
                               "model": behavior["served_model"] or request["model"],
                               "choices": [{"index": 0, "message": {"role": "assistant", "content": OUTPUT},
                                            "finish_reason": "stop"}],
                               "usage": behavior["usage"]}).encode()
            try:
                self.send_response(behavior["status"])
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if behavior["status"] == 307:
                    self.send_header("Location", "/unconfigured-endpoint")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests, received, release, behavior
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_provider_worker_uses_single_dispatch_and_reports_served_model(endpoint, monkeypatch):
    url, requests, _, _, behavior = endpoint
    monkeypatch.setenv("AUTOCONTEXT_OFFLINE", "1")
    behavior["served_model"] = "actual-model"
    completion = complete_routed_model(target(base_url=url), INPUT, timeout_seconds=10, cancel=threading.Event())
    assert completion.model == "fixture-model" and completion.served_model == "actual-model"
    assert completion.usage == {"input_tokens": 100, "output_tokens": 30}
    assert len(requests) == 1 and requests[0]["max_tokens"] == 1024


def test_real_provider_error_is_not_retried(endpoint):
    url, requests, _, _, behavior = endpoint
    behavior["status"] = 500
    with pytest.raises(ModelCallError, match="provider_unavailable"):
        complete_routed_model(target(base_url=url), INPUT, timeout_seconds=10, cancel=threading.Event())
    assert len(requests) == 1


def test_real_provider_redirect_cannot_change_endpoint(endpoint, monkeypatch):
    url, requests, _, _, behavior = endpoint
    monkeypatch.setenv("AUTOCONTEXT_OFFLINE", "1")
    behavior["status"] = 307
    with pytest.raises(ModelCallError, match="provider_unavailable"):
        complete_routed_model(target(base_url=url), INPUT, timeout_seconds=10, cancel=threading.Event())
    assert len(requests) == 1


def test_anthropic_transport_can_enforce_fixed_endpoint(tmp_path):
    # The SDK starts native threads even without a request. Match production's
    # worker boundary so later local-isolation tests retain a single-thread host.
    code = (
        "from autocontext.providers.anthropic import AnthropicProvider; "
        "p=AnthropicProvider(api_key='fixture-key',single_dispatch=True,follow_redirects=False); "
        "assert p._client.max_retries == 0; assert p._client._client.follow_redirects is False; p._client.close()"
    )
    result = run_bounded_process([sys.executable, "-I", "-c", code], cwd=tmp_path, env=default_shell_env(),
                                 shell=False, timeout_ms=10000)
    assert result.exit_code == 0, result.stderr


@pytest.mark.parametrize("usage", [
    {"prompt_tokens": None, "completion_tokens": 30, "total_tokens": 130},
    {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 999},
    {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130, "extra_billed_tokens": 50},
])
def test_real_provider_usage_is_validated_before_normalization(pilot, endpoint, usage):
    url, requests, _, _, behavior = endpoint
    behavior["usage"] = usage
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, general=target(base_url=url)))
    assert result.reason == "provider_accounting_unverified" and len(requests) == 1
    assert result.accounted_tokens == 9216 and result.output_json is None


def test_usage_details_are_subsets_and_additive_cache_tokens_are_not_ignored():
    completion = receipt(raw_usage={"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130,
                                    "prompt_tokens_details": {"cached_tokens": 20},
                                    "completion_tokens_details": {"reasoning_tokens": 10}})
    assert skill_routing._model_receipt(completion, target())[0] == 130
    completion.raw_usage = {"input_tokens": 100, "output_tokens": 30, "cache_read_input_tokens": 100}
    with pytest.raises(ValueError, match="unsupported_provider_usage"):
        skill_routing._model_receipt(completion, target())


def test_real_provider_timeout_keeps_reservation_and_stops_request(pilot, endpoint):
    url, requests, _, _, behavior = endpoint
    behavior["wait"] = True
    selected = target(base_url=url)
    result = run(pilot, config=SkillRoutingConfig(enabled=True, allow_network=True, general=selected,
                                                 budget=RoutingBudget(wall_seconds=10.0)))
    assert result.reason == "request_timeout" and result.output_json is None
    assert len(requests) == 1 and result.model_calls == 1
    assert result.accounted_tokens == selected.max_input_tokens + selected.max_output_tokens
    assert not result.model_cost_complete


@pytest.mark.skipif(os.environ.get("AUTOCONTEXT_RUN_DOCKER_TESTS") != "1", reason="requires explicit Docker lane")
def test_real_docker_routing_skill_then_model(pilot, endpoint, monkeypatch):
    url, requests, _, _, _ = endpoint
    monkeypatch.setenv("AUTOCONTEXT_OFFLINE", "1")
    specific = target(base_url=url)
    register(pilot, specific)
    configured = dict(enabled=True, allow_network=True, specialized=specific)
    success = run(pilot, config=SkillRoutingConfig(bundle_digest=pilot[2].digest, **configured))
    assert success.selected_route == "skill" and not requests, success
    bad = propose_schema_migration(
        pilot[0], SkillReference(entrypoint="choose_action", source=SOURCE.replace('state["name"]', '"wrong"')),
        run_id="routing-fallback", source_evidence=(SkillSourceEvidence.create("example", "example", json.loads(INPUT)),
                                                   SkillSourceEvidence.create("shift", "counterexample", {"schema_version": 99})),
    )
    fallback = run(pilot, config=SkillRoutingConfig(bundle_digest=bad.digest, **configured))
    assert fallback.selected_route == "specialized" and len(requests) == 1, fallback
    assert fallback.attempts[0].reason == "migration_postcondition_failed"
    assert fallback.model_calls == 1 and json.loads(fallback.output_json) == json.loads(OUTPUT)


def test_real_provider_worker_cancels_during_request(endpoint):
    url, requests, received, release, behavior = endpoint
    behavior["wait"] = True
    cancel = threading.Event()
    outcomes = []

    def invoke():
        try:
            complete_routed_model(target(base_url=url), INPUT, timeout_seconds=10, cancel=cancel)
        except ModelCallError as exc:
            outcomes.append(str(exc))

    thread = threading.Thread(target=invoke)
    thread.start()
    try:
        assert received.wait(8)
        cancel.set()
        thread.join(timeout=3)
        assert not thread.is_alive() and outcomes == ["cancelled"] and len(requests) == 1
    finally:
        cancel.set()
        release.set()
        thread.join(timeout=3)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process group assertion")
def test_bounded_worker_cancellation_terminates_descendants(tmp_path):
    pid_path = tmp_path / "descendant.pid"
    code = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
        f"Path({str(pid_path)!r}).write_text(str(p.pid)); time.sleep(60)"
    )
    cancel = threading.Event()
    outcomes = []
    thread = threading.Thread(target=lambda: outcomes.append(run_bounded_process(
        [sys.executable, "-c", code], cwd=tmp_path, env=default_shell_env(), shell=False, timeout_ms=15000, cancel=cancel)))
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid_path.exists()
        cancel.set()
        thread.join(timeout=3)
        assert not thread.is_alive() and outcomes[0].exit_code == 130
        state = subprocess.run(["ps", "-o", "stat=", "-p", pid_path.read_text()], capture_output=True, text=True)
        assert not state.stdout.strip() or state.stdout.strip().startswith("Z")
    finally:
        cancel.set()
        thread.join(timeout=3)
