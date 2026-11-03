from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autocontext.weight_evolution.comparison_models import (
    ArtifactSource,
    CheckpointArtifact,
    ComparisonArtifacts,
    ComparisonBudgets,
    ComparisonCase,
    ComparisonPlan,
    ComparisonProtocol,
    ComparisonSampleCounts,
    ContextArtifact,
    DecodingArtifact,
    DevelopmentProvenance,
    build_comparison_plan,
    load_comparison_plan,
    projected_requests,
)
from autocontext.weight_evolution.datasets import DatasetInstruction, EvaluationProblem


def comparison_context() -> SimpleNamespace:
    baseline = ArtifactSource(kind="baseline_fixture")

    def developed(calls: int, units: int) -> ArtifactSource:
        return ArtifactSource(kind="development_fixture", source_problem_ids=("practice/add",),
                              declared_search_calls=calls, declared_search_units=units)

    development = DevelopmentProvenance(cases=(ComparisonCase(
        dataset="math500", problem=EvaluationProblem(problem_id="practice/add", text="Practice adding 1 and 1.", answer="2"),
    ),))
    cases = (
        ComparisonCase(dataset="math500", problem=EvaluationProblem(
            problem_id="eval/add", text="Add 3 and 4.", answer="gold-only-seven")),
        ComparisonCase(dataset="ifeval", problem=EvaluationProblem(
            problem_id="eval/word", text="Say hello.", instructions=(
                DatasetInstruction(instruction_id="keywords:existence", kwargs={"keywords": ["gold-only-keyword"]}),))),
    )
    artifacts = ComparisonArtifacts(
        base_checkpoint=CheckpointArtifact(artifact_id="base-weights", content="mock checkpoint zero", source=baseline),
        edited_checkpoint=CheckpointArtifact(artifact_id="edited-weights", content="mock checkpoint one",
                                               source=developed(3, 128)),
        base_context=ContextArtifact(artifact_id="base-context", text="", source=baseline),
        learned_context=ContextArtifact(artifact_id="learned-context", text="Check arithmetic carefully.",
                                         source=developed(2, 64)),
        base_decoding=DecodingArtifact(artifact_id="base-decoding", temperature=0.6, top_p=0.95, top_k=20,
                                       presence_penalty=1.5, source=baseline),
        selected_decoding=DecodingArtifact(artifact_id="selected-decoding", temperature=0.2, top_p=0.95, top_k=20,
                                           presence_penalty=1.5, source=developed(1, 32)),
    )
    protocol = ComparisonProtocol(runtime_digest="a" * 64, tokenizer_digest="b" * 64, scorer_digest="c" * 64,
                                  seeds=(0, 17), max_new_tokens=64, math_prompt_suffix="\nGive a boxed answer.")
    budgets = ComparisonBudgets(max_input_units_per_sample=2048, max_generated_units_per_condition=768,
                                max_total_generated_units=3840, max_total_input_units=100000,
                                max_total_wall_seconds=60.0, max_search_calls_per_mechanism=4,
                                max_search_units_per_mechanism=256)
    options = dict(study_id="comparison-fixture", cases=cases, artifacts=artifacts, protocol=protocol,
                   budgets=budgets, development=development)
    return SimpleNamespace(**options, options=options, plan=build_comparison_plan(**options))


def test_exact_five_arm_permissions_and_shared_composition() -> None:
    plan = comparison_context().plan
    arms = {c.condition_id: c for c in plan.conditions}
    assert tuple(arms) == ("A", "C", "W", "CW", "D")
    assert arms["A"].checkpoint_digest == arms["C"].checkpoint_digest == arms["D"].checkpoint_digest
    assert arms["W"].checkpoint_digest == arms["CW"].checkpoint_digest != arms["A"].checkpoint_digest
    assert arms["A"].context_digest == arms["W"].context_digest == arms["D"].context_digest
    assert arms["C"].context_digest == arms["CW"].context_digest != arms["A"].context_digest
    assert len({arms[n].decoding_digest for n in ("A", "C", "W", "CW")}) == 1
    assert arms["D"].decoding_digest != arms["A"].decoding_digest
    assert [c.contrast_id for c in plan.contrasts] == ["C-A", "W-A", "CW-C", "CW-W", "D-A"]
    assert plan.factorial_interaction == "CW-C-W+A"


def test_every_condition_has_the_same_paired_problem_seed_sample_schedule() -> None:
    plan = comparison_context().plan
    keys = {}
    for cell in plan.cells:
        keys.setdefault(cell.condition_id, set()).add((cell.dataset, cell.problem_id, cell.seed, cell.sample_index))
        assert cell.runtime_digest == plan.protocol.runtime_digest
        assert cell.scorer_digest == plan.protocol.scorer_digest
        assert cell.max_new_tokens == plan.protocol.max_new_tokens
        assert cell.input_units == len(cell.rendered_prompt.encode("utf-8"))
    assert all(value == keys["A"] for value in keys.values())
    assert len(plan.cells) == 60 and len({c.digest for c in plan.cells}) == 60
    assert plan.cells[0].condition_id == "A"
    assert next(c for c in plan.cells if c.problem_id == "eval/word").condition_id == "C"


def test_projected_requests_never_include_gold_or_instruction_metadata() -> None:
    payload = json.dumps([c.to_dict() for c in projected_requests(comparison_context().plan)])
    for value in ('"answer"', '"instructions"', "gold-only-seven", "gold-only-keyword"):
        assert value not in payload
    assert "Check arithmetic carefully." in payload


def test_declared_search_costs_count_shared_artifacts_once_but_attribute_cw_sum() -> None:
    plan = comparison_context().plan
    arms = {c.condition_id: c for c in plan.conditions}
    assert arms["A"].declared_search_calls == 0
    assert arms["CW"].declared_search_calls == arms["C"].declared_search_calls + arms["W"].declared_search_calls == 5
    assert arms["CW"].search_attribution == "sum_of_C_and_W_shared_artifacts"
    summary = plan.resource_summary()
    assert summary["declared_unique_search_calls"] == 6
    assert summary["declared_unique_search_units"] == 224
    assert summary["reserved_generated_units"] == 3840
    assert summary["model_execution_performed"] is False and summary["gpu_execution_deferred"] is True


def test_content_identity_ignores_labels_but_study_provenance_binds_them() -> None:
    context = comparison_context()
    renamed = context.artifacts.model_copy(update={
        "learned_context": context.artifacts.learned_context.model_copy(update={"artifact_id": "renamed-context"}),
    })
    other = build_comparison_plan(**{**context.options, "artifacts": renamed})
    assert other.artifacts.learned_context.content_digest == context.artifacts.learned_context.content_digest
    assert other.cells == context.plan.cells
    assert other.digest != context.plan.digest


@pytest.mark.parametrize("field", ["checkpoint_digest", "context_digest", "decoding_digest", "runtime_digest",
                                  "scorer_digest", "dataset_digest", "tokenizer_digest"])
def test_cell_identity_tampering_is_rejected(field: str) -> None:
    raw = comparison_context().plan.to_dict()
    raw["cells"][0][field] = "f" * 64
    with pytest.raises(ValueError, match="schedule"):
        ComparisonPlan.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "reorder", "condition", "seed", "sample", "prompt", "context"])
def test_swapped_or_incomplete_cells_cannot_alias_conditions(mutation: str) -> None:
    raw = comparison_context().plan.to_dict()
    if mutation == "missing":
        raw["cells"].pop()
    elif mutation == "duplicate":
        raw["cells"].append(raw["cells"][0])
    elif mutation == "reorder":
        raw["cells"].reverse()
    else:
        key, value = {"condition": ("condition_id", "C"), "seed": ("seed", 1), "sample": ("sample_index", 8),
                      "prompt": ("user_text", "changed"), "context": ("context_text", "leaked learned context")}[mutation]
        raw["cells"][0][key] = value
    with pytest.raises(ValueError, match="schedule"):
        ComparisonPlan.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field", ["max_input_units_per_sample", "max_generated_units_per_condition",
                                  "max_total_generated_units", "max_total_input_units",
                                  "max_search_calls_per_mechanism", "max_search_units_per_mechanism"])
def test_each_declared_budget_fails_closed(field: str) -> None:
    raw = comparison_context().plan.to_dict()
    raw["budgets"][field] = 1
    with pytest.raises(ValueError, match="budget|reservations"):
        ComparisonPlan.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("mutation", ["id", "normalized_prompt", "unknown_source", "duplicate_practice", "duplicate_eval",
                                      "aliased_practice", "aliased_eval"])
def test_development_provenance_cannot_use_evaluation_cases(mutation: str) -> None:
    raw = comparison_context().plan.to_dict()
    if mutation == "id":
        raw["development"]["cases"][0]["problem"]["problem_id"] = "eval/add"
    elif mutation == "normalized_prompt":
        raw["development"]["cases"][0]["problem"]["text"] = "  ＡＤＤ ３  and   ４. "
    elif mutation == "unknown_source":
        raw["artifacts"]["learned_context"]["source"]["source_problem_ids"] = ["eval/add"]
    elif mutation == "duplicate_practice":
        raw["development"]["cases"].append(raw["development"]["cases"][0])
    elif mutation in {"aliased_practice", "aliased_eval"}:
        split = raw["development"]["cases"] if mutation == "aliased_practice" else raw["cases"]
        other = json.loads(json.dumps(split[0]))
        other["problem"]["problem_id"] = "alias-new-id"
        other["problem"]["text"] = "  " + other["problem"]["text"].upper() + "  "
        split.append(other)
    else:
        raw["cases"].append(raw["cases"][0])
    with pytest.raises(ValueError, match="overlap|duplicate|development pool"):
        ComparisonPlan.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("arm", ["edited_checkpoint", "learned_context", "selected_decoding"])
def test_interventions_must_change_effective_content(arm: str) -> None:
    raw = comparison_context().artifacts.to_dict()
    baseline = {"edited_checkpoint": "base_checkpoint", "learned_context": "base_context",
                "selected_decoding": "base_decoding"}[arm]
    fields = ("content",) if arm == "edited_checkpoint" else ("text",) if arm == "learned_context" else (
        "temperature", "top_p", "top_k", "presence_penalty")
    for name in fields:
        raw[arm][name] = raw[baseline][name]
    with pytest.raises(ValueError, match="must each change"):
        ComparisonArtifacts.model_validate_json(json.dumps(raw))


def test_fixture_scope_is_permanent_and_unknown_fields_rejected() -> None:
    raw = comparison_context().plan.to_dict()
    raw["execution_mode"] = "gpu"
    with pytest.raises(ValueError):
        ComparisonPlan.model_validate_json(json.dumps(raw))
    raw["execution_mode"] = "offline_fixture"
    raw["model_execution_performed"] = True
    with pytest.raises(ValueError):
        ComparisonPlan.model_validate_json(json.dumps(raw))


def test_excessive_cartesian_schedule_is_rejected_before_cell_allocation() -> None:
    context = comparison_context()
    protocol = context.protocol.model_copy(update={"seeds": tuple(range(16)),
                                                   "samples_per_problem": ComparisonSampleCounts(math500=16)})
    with pytest.raises(ValueError, match="cell count"):
        build_comparison_plan(**{**context.options, "cases": (context.cases[0],) * 1000, "protocol": protocol})


def test_roundtrip_digest_and_duplicate_json_rejection(tmp_path: Path) -> None:
    plan = comparison_context().plan
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan.to_dict()))
    assert load_comparison_plan(path) == plan
    assert load_comparison_plan(path).digest == plan.digest
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="duplicate"):
        load_comparison_plan(path)
