"""Study isolation, paired design, accounting and real serving-route integration."""

from __future__ import annotations

import json
import os
import sys
import threading
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from autocontext.execution import skill_routing
from autocontext.execution.docker_skill import DockerSkillExecutor, DockerSkillResult, parse_runner_metrics
from autocontext.execution.routed_model import ModelCallError
from autocontext.execution.skill_routing_models import ModelTarget
from autocontext.providers.base import CompletionResult
from autocontext.util.json_io import read_json, write_json

# Repository experiment code is intentionally outside the installed package.
# The pytest console script and CI shard runner do not add the package cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from benchmarks.skill_reuse import study  # noqa: E402
from benchmarks.skill_reuse.contracts import ARMS, Corpus, LearningRecord, Models, StudyProtocol  # noqa: E402
from benchmarks.skill_reuse.demo import fixture_answer, fixture_endpoint, fixture_models  # noqa: E402
from benchmarks.skill_reuse.reporting import build_report, economics  # noqa: E402


@pytest.fixture
def prepared(tmp_path):
    models = tmp_path / "models.json"
    write_json(models, fixture_models("http://127.0.0.1:9/v1"))
    root = tmp_path / "frozen"
    study.freeze(study.HERE / "protocol.json", study.HERE / "fixtures/corpus.json", models,
                 study.HERE / "fixtures/learning.json", study.HERE / "fixtures/skill.py",
                 study.HERE / "fixtures/playbook.md", root)
    return root


@pytest.fixture
def execution(monkeypatch):
    requests = []

    def complete(target, value, **kwargs):
        requests.append((target, value, kwargs))
        return CompletionResult(text=fixture_answer(value), model=target.model, served_model=target.model,
                                raw_usage={"input_tokens": 100, "output_tokens": 30})

    monkeypatch.setattr(skill_routing, "complete_routed_model", complete)
    monkeypatch.setattr(DockerSkillExecutor, "execute", lambda self, source, value, limits, **kw:
                        DockerSkillResult(fixture_answer(value), image_identity="fixture-image"))
    return requests


def test_runner_resource_receipt_requires_valid_terminal_measurement():
    valid = 'AC_RESOURCE_METRICS:{"cpu_seconds":0.125,"peak_memory_bytes":1048576}\n'
    assert parse_runner_metrics(valid) == (0.125, 1048576)
    for invalid in ('', valid + valid, 'AC_RESOURCE_METRICS:{"cpu_seconds":-1,"peak_memory_bytes":2}\n',
                    'AC_RESOURCE_METRICS:{"cpu_seconds":0.5,"peak_memory_bytes":1.5}\n',
                    'AC_RESOURCE_METRICS:{"cpu_seconds":0.5,"peak_memory_bytes":2,"other":1}\n'):
        assert parse_runner_metrics(invalid) == (None, None)


def test_measured_skill_resources_reach_study_episode(prepared, execution, monkeypatch):
    monkeypatch.setattr(DockerSkillExecutor, "execute", lambda self, source, value, limits, **kw:
                        DockerSkillResult(fixture_answer(value), image_identity="fixture-image",
                                          cpu_seconds=0.125, peak_memory_bytes=1048576, elapsed_seconds=0.2))
    report = study.run(prepared, split="development")
    rows = read_json(prepared / "development/episodes.json")
    measured = [row for row in rows if row["arm"] == "executable" and row["cohort"] == "supported"]
    assert len(measured) == 2 and all(row["cpu_seconds"] == 0.125 for row in measured)
    assert all(row["peak_memory_bytes"] == 1048576 for row in measured)
    assert report["stats"]["supported/executable"]["cpu_seconds"] == 0.25
    assert report["stats"]["supported/executable"]["peak_memory_bytes"] == 1048576
    assert report["stats"]["shifted/executable"]["cpu_seconds"] is None


def test_packet_candidate_uses_matched_training_and_stays_development_only(tmp_path, execution):
    candidate = study.HERE / "fixtures"
    corpus = Corpus.model_validate_json((candidate / "corpus.json").read_text())
    learning = LearningRecord.model_validate_json((candidate / "packet_candidate_learning.json").read_text())
    assert learning.training_digest == study.learning_packet(corpus)["training_digest"]
    assert learning.origin == "fixture"
    models = tmp_path / "models.json"
    write_json(models, fixture_models("http://127.0.0.1:9/v1"))
    root = tmp_path / "frozen"
    study.freeze(study.HERE / "protocol.json", candidate / "corpus.json", models,
                 candidate / "packet_candidate_learning.json", candidate / "packet_candidate_skill.py",
                 candidate / "packet_candidate_playbook.md", root)
    report = study.run(root, split="development")
    assert report["decision"] == "infrastructure_only" and not report["production_activation_authorized"]
    assert all(row["successes"] == row["cases"] for row in report["stats"].values())
    data = fixture_models("http://127.0.0.1:9/v1")
    data["evidence_kind"] = "live"
    write_json(models, data)
    with pytest.raises(ValueError, match="fixture artifacts cannot become live"):
        study.freeze(study.HERE / "protocol.json", candidate / "corpus.json", models,
                     candidate / "packet_candidate_learning.json", candidate / "packet_candidate_skill.py",
                     candidate / "packet_candidate_playbook.md", tmp_path / "not-live")


def test_post_candidate_corpus_preserves_learning_split_and_changes_every_heldout_case():
    original = Corpus.model_validate_json((study.HERE / "fixtures/corpus.json").read_text())
    fresh = Corpus.model_validate_json((study.HERE / "fixtures/post_candidate_corpus.json").read_text())
    assert fresh.training == original.training and fresh.development == original.development
    assert fresh.training_digest == original.training_digest
    assert not ({c.id for c in original.heldout} & {c.id for c in fresh.heldout})
    assert not ({c.input_digest for c in original.heldout} & {c.input_digest for c in fresh.heldout})
    for cohort in ("supported", "shifted"):
        cases = [c for c in fresh.heldout if c.cohort == cohort]
        assert len(cases) == 12 and len({c.group for c in cases}) == 4
    assert {case.id for case in fresh.heldout} == {f"fresh-{i:02d}" for i in range(1, 25)}


def test_bounded_training_only_synthesis_produces_freeze_ready_evidence(tmp_path, monkeypatch):
    corpus = study.HERE / "fixtures/post_candidate_corpus.json"
    model = ModelTarget(provider="openai-compatible", model="anthropic/claude-sonnet-4.6",
                        base_url="https://openrouter.ai/api/v1", api_key_env="OPENROUTER_API_KEY",
                        provider_only="anthropic", input_cost_per_1k=0.003, output_cost_per_1k=0.015)
    models = tmp_path / "models.json"
    write_json(models, {"evidence_kind": "live", "reference": model.model_dump(mode="json"),
                        "cheaper": {**model.model_dump(mode="json"), "model": "anthropic/claude-haiku-4.5",
                                    "input_cost_per_1k": .001, "output_cost_per_1k": .005}})
    requests = []

    def complete(target, value, **kwargs):
        requests.append((value, kwargs["system_prompt"]))
        name = "skill.py" if "Python source" in kwargs["system_prompt"] else "playbook.md"
        return CompletionResult(text=(study.HERE / "fixtures" / f"packet_candidate_{name}").read_text(),
                                model=target.model, served_model=target.model,
                                raw_usage={"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180,
                                           "cost": 0.002, "is_byok": False})

    monkeypatch.setattr(study, "complete_routed_model", complete)
    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-not-a-credential")
    output = tmp_path / "learning"
    study.synthesize(study.HERE / "protocol.json", corpus, models, output)
    record = LearningRecord.model_validate_json((output / "learning.json").read_text())
    assert record.origin == "externally_prepared" and len(requests) == 2
    assert record.training_digest == Corpus.model_validate_json(corpus.read_text()).training_digest
    assert all("fresh-" not in value for request in requests for value in request)
    assert {entry.id for entry in record.discovery} == {"playbook", "skill", "baseline", "authoring-overhead"}
    root = tmp_path / "freeze"
    study.freeze(study.HERE / "protocol.json", corpus, models, output / "learning.json",
                 output / "skill.py", output / "playbook.md", root)
    assert (root / "freeze.json").exists()


def test_failed_prior_synthesis_is_reconciled_before_new_calls(tmp_path, monkeypatch):
    corpus = study.HERE / "fixtures/post_candidate_corpus.json"
    models = study.HERE / "openrouter-models.json"
    prior = tmp_path / "prior"
    (prior / "calls").mkdir(parents=True)
    packet = study.learning_packet(Corpus.model_validate_json(corpus.read_text()))
    write_json(prior / "ledger.json", [{"phase": "playbook", "state": "unverified", "seconds": 2.5}])
    write_json(prior / "calls/playbook.request.json", {"model": "anthropic/claude-sonnet-4.6",
                                                        "training_packet": packet})
    write_json(prior / "calls/playbook.response.json", {
        "model": "anthropic/claude-sonnet-4.6", "output": '{"schema_version":2}',
        "raw_usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130,
                      "cost": 0.001, "is_byok": False}})

    def complete(target, value, **kwargs):
        name = "skill.py" if "Python source" in kwargs["system_prompt"] else "playbook.md"
        return CompletionResult(text=(study.HERE / "fixtures" / f"packet_candidate_{name}").read_text(),
                                model=target.model, served_model=target.model,
                                raw_usage={"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130,
                                           "cost": 0.001, "is_byok": False})

    monkeypatch.setenv("OPENROUTER_API_KEY", "fixture-not-a-credential")
    monkeypatch.setattr(study, "complete_routed_model", complete)
    output = tmp_path / "learning"
    study.synthesize(study.HERE / "openrouter-protocol.json", corpus, models, output, prior_attempt=prior)
    learning = LearningRecord.model_validate_json((output / "learning.json").read_text())
    assert learning.discovery[0].id == "failed-playbook-001"
    assert learning.discovery[0].model_calls == 1 and learning.discovery[0].total_cost_usd == .001
    assert read_json(output / "ledger.json")[0]["state"] == "reconciled_failed_candidate"


def test_learning_packet_excludes_development_and_heldout():
    corpus = Corpus.model_validate_json((study.HERE / "fixtures/corpus.json").read_text())
    packet = study.learning_packet(corpus)
    assert packet["training_digest"] == corpus.training_digest
    assert {c["id"] for c in packet["training"]} == {c.id for c in corpus.training}
    assert not ({c.id for c in corpus.heldout + corpus.development} & {c["id"] for c in packet["training"]})
    assert set(packet) == {"training_digest", "training"}


@pytest.mark.parametrize("leak", ["input", "group", "id", "cohort"])
def test_split_leakage_and_incorrect_cohorts_are_rejected(leak):
    data = read_json(study.HERE / "fixtures/corpus.json")
    held, train = data["heldout"][0], data["training"][0]
    if leak == "input":
        held["input_json"] = json.dumps(json.loads(train["input_json"]), indent=4)
    elif leak == "cohort":
        held["cohort"] = "shifted"
    else:
        held[leak] = train[leak]
    with pytest.raises(ValueError):
        Corpus.model_validate(data)


def test_balanced_pair_order_is_deterministic():
    corpus = Corpus.model_validate_json((study.HERE / "fixtures/corpus.json").read_text())
    plan = study.schedule(corpus.heldout, 1029)
    assert plan == study.schedule(corpus.heldout, 1029)
    assert plan != study.schedule(corpus.heldout, 1030)
    for cohort in ("supported", "shifted"):
        for position in range(4):
            assert set(Counter(arms[position] for case, arms in plan if case.cohort == cohort).values()) == {3}


@pytest.mark.parametrize("name", ["skill.py", "playbook.md", "protocol.json", "models.json", "corpus.json", "learning.json"])
def test_modified_artifacts_cannot_dispatch(prepared, execution, name):
    path = prepared / name
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match="frozen artifact changed"):
        study.run(prepared)
    assert not execution and not (prepared / "heldout").exists()


def test_environment_change_invalidates_evidence(prepared, execution, monkeypatch):
    monkeypatch.setattr(study, "fingerprint", lambda: {"changed": True})
    with pytest.raises(ValueError, match="environment changed"):
        study.run(prepared)
    assert not execution


def test_four_arms_share_routes_and_preserve_active_pointer(prepared, execution):
    before = study.load_frozen(prepared)[-1].active_pointer("schema_migration")
    report = study.run(prepared)
    assert report["status"] == "complete" and report["decision"] == "infrastructure_only"
    assert report["quality_and_coverage_checks_pass"]
    assert not report["production_activation_authorized"]
    rows = read_json(prepared / "heldout/episodes.json")
    assert len(rows) == 96 and len(execution) == 84
    for case_id in {r["case_id"] for r in rows}:
        paired = [r for r in rows if r["case_id"] == case_id]
        assert {r["arm"] for r in paired} == set(ARMS)
        assert len({r["input_digest"] for r in paired}) == 1
    assert report["stats"]["supported/executable"]["skill_coverage"] == 1
    assert report["stats"]["shifted/executable"]["fallback_frequency"] == 1
    assert report["stats"]["supported/baseline"]["fallback_frequency"] == 0
    assert report["actual_lifecycle_resources_per_success"]["executable"]["total_cost_usd"] is None
    assert study.load_frozen(prepared)[-1].active_pointer("schema_migration") == before
    traces = [read_json(Path(r["trace_path"])) for r in rows]
    for row, trace in zip(rows, traces, strict=True):
        config = json.loads(trace["config_json"])
        assert bool(config["learned_playbook"]) == (row["arm"] != "baseline")
        assert bool(config["bundle_digest"]) == (row["arm"] == "executable")
        assert trace["mode"] == "evaluation"
    with pytest.raises(FileExistsError):
        study.run(prepared)


def test_rejected_wrong_proposal_is_not_scored_as_correct_abstention(prepared, execution, monkeypatch):
    monkeypatch.setattr(skill_routing, "complete_routed_model", lambda target, value, **kw: CompletionResult(
        text='{"schema_version":2,"display_name":"Wrong","status":"enabled"}', model=target.model,
        served_model=target.model, raw_usage={"input_tokens": 100, "output_tokens": 30}))
    report = study.run(prepared, split="development")
    assert report["stats"]["shifted/textual"]["successes"] == 0
    assert report["stats"]["shifted/textual"]["harmful_proposals_rejected"] == 2


def test_pre_cancelled_study_makes_no_calls_and_has_no_positive_decision(prepared, execution):
    cancel = threading.Event()
    cancel.set()
    report = study.run(prepared, cancel=cancel)
    assert report["status"] == "cancelled" and not execution
    assert not report["quality_and_coverage_checks_pass"] and not report["complete_pairs"]


def test_unknown_receipt_retains_reservation_and_stops_study(prepared, execution, monkeypatch):
    monkeypatch.setattr(skill_routing, "complete_routed_model", lambda *a, **kw:
                        (_ for _ in ()).throw(ModelCallError("invalid_provider_receipt")))
    report = study.run(prepared)
    assert report["status"] == "unverified_or_cancelled_execution"
    rows = read_json(prepared / "heldout/episodes.json")
    calls = [r for r in rows if r["model_calls"]]
    assert len(calls) == 1 and calls[0]["tokens"] == 9216
    assert not report["complete_pairs"]


def test_crash_keeps_durable_reservation_and_partial_report(prepared, monkeypatch):
    monkeypatch.setattr(study, "route_schema_migration", lambda *a, **kw:
                        (_ for _ in ()).throw(RuntimeError("simulated crash")))
    with pytest.raises(RuntimeError, match="simulated crash"):
        study.run(prepared)
    report = read_json(prepared / "heldout/summary.json")
    assert report["status"] == "interrupted" and len(report["unreconciled_reservations"]) == 1
    assert report["unreconciled_reservations"][0]["reserved_tokens"] == 9216
    arm = report["unreconciled_reservations"][0]["arm"]
    assert report["qualification_costs_including_evaluation"][arm]["model_calls"] is None
    assert report["cost_accounting_incomplete_arms"] == [arm]
    with pytest.raises(FileExistsError):
        study.run(prepared)


@pytest.mark.parametrize("stop", ["deadline", "cancel"])
def test_ledger_write_cannot_dispatch_after_study_stops(prepared, execution, monkeypatch, stop):
    now = [0.0]
    cancel = threading.Event()
    monkeypatch.setattr(study.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(study, "schedule", lambda cases, seed: [(c, list(ARMS)) for c in cases])
    original = study.write_json

    def slow_write(path, value):
        original(path, value)
        if path.name == "ledger.json" and value[-1]["state"] == "reserved":
            if stop == "deadline":
                now[0] = 3601.0
            else:
                cancel.set()

    monkeypatch.setattr(study, "write_json", slow_write)
    monkeypatch.setattr(study, "route_schema_migration", lambda *a, **kw: pytest.fail("dispatch after stop"))
    report = study.run(prepared, cancel=cancel)
    assert report["status"] == ("evaluation_budget_exhausted" if stop == "deadline" else "cancelled")
    assert not execution and not report["unreconciled_reservations"]
    ledger = read_json(prepared / "heldout/ledger.json")
    assert len(ledger) == 1 and ledger[0]["state"] == "not_dispatched" and ledger[0]["model_calls"] == 0


def test_trace_failure_after_call_makes_affected_costs_unknown(prepared, execution, monkeypatch):
    original_route = study.route_schema_migration
    original_write = skill_routing.write_json
    textual_attempts = [0]
    current_textual = [False]

    def route(*args, **kwargs):
        config = kwargs["config"]
        current_textual[0] = (bool(config.learned_playbook) and config.bundle_digest is None
                              and config.general.model == "fixture-reference")
        textual_attempts[0] += current_textual[0]
        return original_route(*args, **kwargs)

    def fail_final_trace(path, data):
        if current_textual[0] and textual_attempts[0] == 2 and data.get("status") in {"success", "abstention"}:
            raise OSError("final trace write failure after provider completion")
        return original_write(path, data)

    monkeypatch.setattr(study, "route_schema_migration", route)
    monkeypatch.setattr(skill_routing, "write_json", fail_final_trace)
    with pytest.raises(OSError, match="final trace"):
        study.run(prepared)
    report = read_json(prepared / "heldout/summary.json")
    assert textual_attempts[0] == 2
    assert report["cost_accounting_incomplete_arms"] == ["textual"]
    assert report["qualification_costs_including_evaluation"]["textual"]["model_calls"] is None
    assert report["actual_lifecycle_resources_per_success"]["textual"]["tokens"] is None
    assert all(r["tokens"] is None for r in report["projected_horizons"]["textual"])
    assert all(v is None for v in report["observed_first_resource_crossing_vs"]["textual"].values())
    reservation, = report["unreconciled_reservations"]
    assert reservation["arm"] == "textual" and reservation["reserved_tokens"] == 9216
    assert report["stats"][f"{reservation['cohort']}/textual"]["model_calls"] is None
    assert "unknown" in (prepared / "heldout/REPORT.md").read_text()


def test_cost_ledger_includes_failed_discovery_and_unknown_resources(prepared, execution):
    study.run(prepared, split="development")
    rows = read_json(prepared / "development/episodes.json")
    learning = LearningRecord.model_validate({
        "training_digest": "a" * 64, "origin": "externally_prepared", "method": "test", "evidence_refs": ["test"],
        "discovery": [{"id": "failed", "phase": "failed_candidate", "arms": ["executable"], "seconds": 30,
                       "model_calls": 2, "tokens": 800, "total_cost_usd": None, "evidence": "failed attempts"},
                      {"id": "common", "phase": "discovery", "arms": list(ARMS), "seconds": 0,
                       "model_calls": 0, "tokens": 0, "total_cost_usd": None, "evidence": "common fixture setup"}]})
    protocol = StudyProtocol()
    report = economics(rows, protocol, learning)
    assert report["one_time_costs"]["executable"]["tokens"] == 800
    # Two unsupported cases call the fallback: 800 setup + 260 execution tokens, four correct tasks.
    assert report["actual_lifecycle_resources_per_success"]["executable"]["tokens"] == 265
    assert report["projected_horizons"]["executable"][0]["tokens"] == 1060
    assert report["projected_horizons"]["executable"][0]["total_cost_usd"] is None
    assert report["observed_first_resource_crossing_vs"]["textual"]["tokens"] is None


def test_total_cost_requires_measured_prices_and_verified_model_receipt(prepared, execution):
    skill = SimpleNamespace(route="skill", execution_seconds=0.2, accounting="none")
    model = SimpleNamespace(route="general", execution_seconds=0, accounting="provider-reported")
    result = SimpleNamespace(attempts=[skill, model], model_cost_complete=True, accounted_model_cost_usd=0.03)
    protocol = StudyProtocol(local_route_cost_per_second_usd=0.001, isolated_skill_cost_per_second_usd=0.002)
    assert study.measured_task_cost(result, protocol, 2) == pytest.approx(0.0324)
    assert study.measured_task_cost(result, StudyProtocol(), 2) is None
    result.model_cost_complete = False
    model.accounting = "declared-pricing"
    assert study.measured_task_cost(result, protocol, 2) is None
    assert study.estimated_task_cost(result, protocol, 2) == pytest.approx(0.0324)
    model.accounting = "reservation"
    assert study.estimated_task_cost(result, protocol, 2) is None


def test_live_report_cannot_claim_economic_go_from_quality_only(prepared, execution):
    study.run(prepared)
    _, _, protocol, _, _, learning, _, _ = study.load_frozen(prepared)
    report = build_report(read_json(prepared / "heldout/episodes.json"), protocol, learning,
                          evidence_kind="live", split="heldout", expected_cases=24, status="complete")
    assert report["quality_and_coverage_checks_pass"]
    assert report["decision"] == "inconclusive_total_lifecycle_cost_unmeasured"


def test_economic_decision_requires_lifecycle_savings_against_both_text_controls(prepared, execution):
    study.run(prepared)
    _, _, protocol, _, _, learning, _, _ = study.load_frozen(prepared)
    ledger = [{**entry.model_dump(mode="json"), "total_cost_usd": 0.1 if "executable" in entry.arms else 0.0}
              for entry in learning.discovery]
    accounted = LearningRecord.model_validate({**learning.model_dump(mode="json"), "discovery": ledger})
    rows = read_json(prepared / "heldout/episodes.json")
    for row in rows:
        row["total_cost_usd"] = {"baseline": 0.01, "textual": 0.02, "cheap_textual": 0.005,
                                 "executable": 0.001}[row["arm"]]
        if row["arm"] == "executable" and row["skill_proposals"]:
            row.update(cpu_seconds=0.1, peak_memory_bytes=1048576)
    report = build_report(rows, protocol, accounted, evidence_kind="live", split="heldout", expected_cases=24,
                          status="complete")
    assert report["decision"] == "conditional_go_for_promotion_review"
    assert not report["production_activation_authorized"]
    for row in rows:
        if row["arm"] == "executable":
            row["total_cost_usd"] = 0.1
    report = build_report(rows, protocol, accounted, evidence_kind="live", split="heldout", expected_cases=24,
                          status="complete")
    assert report["decision"] == "no_go_economics"


def test_development_costs_are_retained_in_final_run(prepared, execution):
    study.run(prepared, split="development")
    report = study.run(prepared)
    assert report["one_time_costs"]["textual"]["tokens"] == 520
    assert report["one_time_costs"]["executable"]["tokens"] == 260
    assert len(report["discovery_ledger"]) == 5


def test_whole_pair_budget_stops_before_an_unaffordable_pair(tmp_path, execution):
    models = tmp_path / "models.json"
    write_json(models, fixture_models("http://127.0.0.1:9/v1"))
    protocol = tmp_path / "protocol.json"
    write_json(protocol, StudyProtocol(max_evaluation_calls=4).model_dump(mode="json"))
    root = tmp_path / "frozen"
    study.freeze(protocol, study.HERE / "fixtures/corpus.json", models, study.HERE / "fixtures/learning.json",
                 study.HERE / "fixtures/skill.py", study.HERE / "fixtures/playbook.md", root)
    report = study.run(root)
    assert report["status"] == "evaluation_budget_exhausted"
    rows = read_json(root / "heldout/episodes.json")
    assert len(rows) == 4 and len({r["case_id"] for r in rows}) == 1 and len(execution) <= 4
    assert not report["quality_and_coverage_checks_pass"]


def test_post_freeze_changes_on_last_trial_invalidate_report(prepared, execution, monkeypatch):
    real = study.route_schema_migration
    count = [0]

    def change(*args, **kwargs):
        result = real(*args, **kwargs)
        count[0] += 1
        if count[0] == 16:
            (prepared / "playbook.md").write_text("changed after last dispatch")
        return result

    monkeypatch.setattr(study, "route_schema_migration", change)
    report = study.run(prepared, split="development")
    assert report["status"] == "frozen_identity_changed" and not report["complete_pairs"]


def test_fixture_models_cannot_call_external_endpoints_or_share_same_model():
    for endpoint in ("https://api.openai.com/v1", "https://example.com/v1"):
        with pytest.raises(ValueError, match="loopback"):
            Models.model_validate(fixture_models(endpoint))
    data = fixture_models("http://127.0.0.1:9/v1")
    data["cheaper"]["model"] = data["reference"]["model"]
    with pytest.raises(ValueError, match="distinct model"):
        Models.model_validate(data)


@pytest.mark.skipif(os.environ.get("AUTOCONTEXT_RUN_DOCKER_TESTS") != "1", reason="requires explicit real Docker lane")
def test_real_docker_packet_candidate_development(tmp_path):
    candidate = study.HERE / "fixtures"
    with fixture_endpoint() as (endpoint, requests):
        models = tmp_path / "models.json"
        write_json(models, fixture_models(endpoint))
        root = tmp_path / "frozen"
        study.freeze(study.HERE / "protocol.json", candidate / "post_candidate_corpus.json", models,
                     candidate / "packet_candidate_learning.json", candidate / "packet_candidate_skill.py",
                     candidate / "packet_candidate_playbook.md", root)
        report = study.run(root, split="development")
    assert report["decision"] == "infrastructure_only"
    assert all(row["successes"] == row["cases"] == 2 for row in report["stats"].values())
    assert report["stats"]["supported/executable"]["cpu_seconds"] is not None
    assert report["stats"]["supported/executable"]["peak_memory_bytes"] is not None
    assert len(requests) == 14


@pytest.mark.skipif(os.environ.get("AUTOCONTEXT_RUN_DOCKER_TESTS") != "1", reason="requires explicit real Docker lane")
def test_real_docker_four_arm_study(tmp_path):
    with fixture_endpoint() as (endpoint, requests):
        models = tmp_path / "models.json"
        write_json(models, fixture_models(endpoint))
        root = tmp_path / "frozen"
        study.freeze(study.HERE / "protocol.json", study.HERE / "fixtures/corpus.json", models,
                     study.HERE / "fixtures/learning.json", study.HERE / "fixtures/skill.py",
                     study.HERE / "fixtures/playbook.md", root)
        report = study.run(root, split="development")
    assert report["status"] == "complete" and report["decision"] == "infrastructure_only"
    assert all(row["successes"] == row["cases"] == 2 for row in report["stats"].values())
    assert len(requests) == 14
    assert sum("Learned playbook" in r["messages"][0]["content"] for r in requests) == 10
    assert report["stats"]["supported/executable"]["model_calls"] == 0
    assert report["stats"]["supported/executable"]["cpu_seconds"] is not None
    assert report["stats"]["supported/executable"]["peak_memory_bytes"] is not None
