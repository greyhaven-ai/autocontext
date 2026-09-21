from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from autocontext.execution.evaluator_epoch import EvaluatorEpoch, compute_evaluator_epoch
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry, observe_epoch_quarantined
from autocontext.execution.judge import LLMJudge
from autocontext.execution.judge_spec import JudgeServingSpec
from autocontext.extensions import HookBus, HookEvents


def judge(**kwargs) -> LLMJudge:
    return LLMJudge(model="m", rubric="Correctness", llm_fn=lambda *_: '{"score": 0.8, "reasoning": "ok"}', **kwargs)


def examples() -> list[dict]:
    return [{"agent_output": "a", "human_score": 0.8, "human_notes": "accurate"},
            {"agent_output": "b", "human_score": 0.1, "human_notes": "wrong"}]


def test_shared_wire_fixture_and_immutability() -> None:
    cases = json.loads((Path(__file__).parent / "fixtures/judge-serving-spec-cases.json").read_text())["cases"]
    assert len({c["epoch_id"] for c in cases}) == len(cases)
    for case in cases:
        spec = JudgeServingSpec.model_validate_json(json.dumps(case["spec"]))
        assert spec.canonical_json() == case["canonical_json"]
        assert spec.epoch_id == case["epoch_id"]
        with pytest.raises(ValueError):
            spec.judge_model = "mutated"
        with pytest.raises(ValueError):
            JudgeServingSpec.model_validate_json(json.dumps({**case["spec"], "api_key": "not-allowed"}))


def test_served_examples_change_identity_but_database_metadata_does_not() -> None:
    j = judge()
    original = examples()
    before = j.evaluate("task", "output", calibration_examples=original)
    snapshot = before.evaluator_spec
    original[0]["human_notes"] = "actually wrong"
    changed = j.evaluate("task", "output", calibration_examples=original)
    reversed_result = j.evaluate("task", "output", calibration_examples=original[::-1])
    assert len({before.evaluator_epoch, changed.evaluator_epoch, reversed_result.evaluator_epoch}) == 3
    original[0]["api_key"] = "never persist arbitrary database fields"
    assert j.evaluate("task", "output", calibration_examples=original).evaluator_epoch == changed.evaluator_epoch
    assert before.evaluator_spec == snapshot
    assert "api_key" not in changed.evaluator_spec
    assert all(line in j._build_judge_prompt("task", "output", calibration_examples=original)
               for line in JudgeServingSpec.model_validate_json(changed.evaluator_spec).serving_examples)


def test_fixture_and_execution_provenance_are_outside_evaluator_identity() -> None:
    a = judge().evaluate("task A", "output A", reference_context="reference A")
    b = judge(samples=2, temperature=0.4, max_tokens=8192).evaluate(
        "task B", "output B", reference_context="reference B", required_concepts=["other fact"],
    )
    assert a.evaluator_epoch == b.evaluator_epoch
    assert a.fixture_provenance != b.fixture_provenance
    assert b.execution_provenance["samples"] == 2
    assert b.execution_provenance["max_tokens"] == 8192
    assert b.execution_provenance["requests"][0]["temperature"] == 0.4
    assert "reference B" not in b.evaluator_spec
    assert judge().evaluate("task", "output", pinned_dimensions=["correctness"]).evaluator_epoch != a.evaluator_epoch
    assert a.evaluator_epoch != compute_evaluator_epoch("Correctness", judge().provider.name, "m").epoch_id


@pytest.mark.parametrize("event,patch", [(HookEvents.BEFORE_JUDGE, {"system_prompt": "different rules"}),
                                       (HookEvents.AFTER_JUDGE, {"response_text": '{"score": 1}'})])
def test_unbound_hook_effects_do_not_claim_a_verified_spec(event, patch) -> None:
    bus = HookBus()
    bus.on(event, lambda _: patch)
    result = judge(hook_bus=bus).evaluate("task", "output")
    assert result.evaluator_epoch is None
    assert result.evaluator_spec is None
    assert result.execution_provenance["identity_status"] == "unbound_hook_or_model_mix"


def test_registry_roundtrip_and_mismatched_manifest_fails_closed(tmp_path) -> None:
    spec = judge().serving_spec(calibration_examples=examples())
    registry = EvaluatorEpochRegistry(tmp_path)
    epoch = EvaluatorEpoch.from_spec(spec)
    assert observe_epoch_quarantined(tmp_path, "task", epoch.epoch_id, serving_spec=epoch.serving_spec) is False
    record = registry.load("task", epoch.epoch_id)
    assert record.serving_spec == spec.canonical_json()
    reconstructed = JudgeServingSpec.model_validate_json(record.serving_spec)
    assert EvaluatorEpoch.from_spec(reconstructed) == epoch
    changed = judge().serving_spec(calibration_examples=examples()[::-1])
    assert observe_epoch_quarantined(tmp_path, "task", changed.epoch_id, serving_spec=epoch.serving_spec) is True
    assert registry.load("task", changed.epoch_id) is None
    record.serving_spec = None
    with pytest.raises(ValueError, match="immutable"):
        registry.register(record)
    assert registry.load("task", epoch.epoch_id).serving_spec == epoch.serving_spec


def test_historical_registry_record_is_not_upgraded(tmp_path) -> None:
    registry = EvaluatorEpochRegistry(tmp_path)
    old = compute_evaluator_epoch("Correctness", judge().provider.name, "m")
    registry.observe("task", old)
    new = EvaluatorEpoch.from_spec(judge().serving_spec())
    assert registry.observe("task", new).activation_state == "candidate"
    assert registry.load("task", old.epoch_id).serving_spec is None


def test_example_change_rejudges_identical_artifact_and_rebaselines() -> None:
    from autocontext.execution.improvement_loop import ImprovementLoop
    from autocontext.scenarios.agent_task import AgentTaskResult

    serving = examples()
    j = judge()

    class Task:
        def get_rubric(self):
            return "Correctness"

        def get_task_prompt(self, state):
            return "task"

        def evaluate_output(self, output, state, **kwargs):
            verdict = j.evaluate("task", output, **kwargs)
            values = asdict(verdict)
            return AgentTaskResult(**{k: values[k] for k in AgentTaskResult.__dataclass_fields__ if k in values})

        def revise_output(self, output, feedback, state):
            serving[0]["human_notes"] = "changed standard"
            return output

        def verify_facts(self, output, state):
            return None

    result = ImprovementLoop(Task(), max_rounds=2, min_rounds=2, quality_threshold=2).run(
        "identical output", {}, calibration_examples=serving,
    )
    assert result.judge_calls == 2
    assert result.rounds[0].evaluator_epoch != result.rounds[1].evaluator_epoch
    assert result.best_round == 2
    assert result.evaluator_spec == result.rounds[1].evaluator_spec
