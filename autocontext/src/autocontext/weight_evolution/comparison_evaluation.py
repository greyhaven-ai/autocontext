"""Offline fixture evidence and descriptive comparisons for frozen conditions.

No model, tokenizer, GPU, or official benchmark result is produced here. UTF-8
byte counts and elapsed values are fixture accounting, never measured model use.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, Self

from pydantic import Field, model_validator

from autocontext.analytics.paired_statistics import paired_confidence_interval
from autocontext.util.json_io import write_text_atomic
from autocontext.weight_evolution.artifacts import canonical_digest, load_small_json
from autocontext.weight_evolution.comparison_models import ComparisonCell, ComparisonPlan
from autocontext.weight_evolution.datasets import EvaluationProblem
from autocontext.weight_evolution.models import Digest, FrozenModel
from autocontext.weight_evolution.scorers import ScoredSample, ScorerInfrastructureError, grouped_sample_accuracy

CONDITIONS = ("A", "C", "W", "CW", "D")
CONTRASTS = (("C-A", "C", "A"), ("W-A", "W", "A"), ("CW-C", "CW", "C"),
             ("CW-W", "CW", "W"), ("D-A", "D", "A"))
MAX_FIXTURE_BYTES = 16 * 1024 * 1024


class FixtureObservation(FrozenModel):
    execution_kind: Literal["fixture"] = "fixture"
    cell_digest: Digest
    condition_id: Literal["A", "C", "W", "CW", "D"]
    dataset: Literal["math500", "gsm8k", "ifeval"]
    problem_id: str = Field(min_length=1, max_length=512)
    sample_index: int = Field(ge=0, le=1000)
    seed: int = Field(ge=0, le=4294967295)
    text: str = Field(max_length=131072)
    response_sha256: Digest
    finish_reason: Literal["stop", "length"]
    input_units: int = Field(ge=1, le=1048576)
    output_units: int = Field(ge=0, le=1048576)
    elapsed_seconds: float = Field(ge=0, le=86400, allow_inf_nan=False)

    @model_validator(mode="after")
    def response_identity(self) -> Self:
        payload = self.text.encode("utf-8")
        if self.response_sha256 != hashlib.sha256(payload).hexdigest() or self.output_units != len(payload):
            raise ValueError("fixture response hash or UTF-8 byte count differs from text")
        return self


class FixtureEvidence(FrozenModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["fixture"] = "fixture"
    model_execution_performed: Literal[False] = False
    plan_digest: Digest
    observations: tuple[FixtureObservation, ...] = Field(min_length=1, max_length=100000)

    @model_validator(mode="after")
    def unique_cells(self) -> Self:
        if len({item.cell_digest for item in self.observations}) != len(self.observations):
            raise ValueError("duplicate fixture cell evidence")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


def load_comparison_evidence(path: Path) -> FixtureEvidence:
    value = load_small_json(path, max_bytes=MAX_FIXTURE_BYTES)
    return FixtureEvidence.model_validate_json(json.dumps(value, allow_nan=False))


def fixture_observation(
    cell: ComparisonCell, text: str, *, finish_reason: Literal["stop", "length"] = "stop",
    elapsed_seconds: float = 0.0,
) -> FixtureObservation:
    """Bind a prerecorded response to a projected cell; this never receives gold."""
    return FixtureObservation(
        cell_digest=cell.digest, condition_id=cell.condition_id, dataset=cell.dataset, problem_id=cell.problem_id,
        sample_index=cell.sample_index, seed=cell.seed, text=text,
        response_sha256=hashlib.sha256(text.encode()).hexdigest(), finish_reason=finish_reason,
        input_units=cell.input_units, output_units=len(text.encode()), elapsed_seconds=elapsed_seconds,
    )


class ComparisonScorer(Protocol):
    @property
    def lock_digest(self) -> str: ...

    @property
    def execution_kind(self) -> str: ...

    def provenance(self) -> dict[str, Any]: ...

    def validate_cohort(self, dataset: str, problems: Sequence[EvaluationProblem]) -> None: ...

    def score_sample(self, dataset: str, problem: EvaluationProblem, sample: FixtureObservation) -> int: ...


@dataclass(frozen=True)
class ComparisonEvaluation:
    payload: dict[str, Any]

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        required = {"evidence_kind": "offline_comparison_fixture", "execution_kind": "fixture",
                    "model_execution_performed": False, "gpu_execution_deferred": True,
                    "benchmark_result_claimed": False, "promotion_claimed": False, "retained_learning_claimed": False}
        if any(type(self.payload.get(key)) is not type(value) or self.payload.get(key) != value
               for key, value in required.items()):
            raise ValueError("comparison report fixture claims cannot be relabelled")
        return json.loads(json.dumps(self.payload, allow_nan=False))  # type: ignore[no-any-return]


def _validate_evidence(plan: ComparisonPlan, evidence: FixtureEvidence) -> dict[str, FixtureObservation]:
    if evidence.plan_digest != plan.digest:
        raise ValueError("fixture evidence belongs to a different comparison plan")
    cells = {cell.digest: cell for cell in plan.cells}
    observed = {item.cell_digest: item for item in evidence.observations}
    if set(observed) != set(cells):
        raise ValueError("fixture evidence must cover every planned condition/case/seed/sample exactly once")
    generated = dict.fromkeys(CONDITIONS, 0)
    inputs = 0
    for digest, cell in cells.items():
        item = observed[digest]
        if (item.condition_id, item.dataset, item.problem_id, item.sample_index, item.seed) != (
            cell.condition_id, cell.dataset, cell.problem_id, cell.sample_index, cell.seed,
        ):
            raise ValueError("fixture labels differ from their immutable cell identity")
        if item.input_units != cell.input_units or item.input_units > plan.budgets.max_input_units_per_sample:
            raise ValueError("fixture input units differ from rendered prompt or exceed their budget")
        if item.output_units > cell.max_new_tokens or (
            item.finish_reason == "length" and item.output_units != cell.max_new_tokens
        ):
            raise ValueError("fixture output units or length termination differ from the declared cap")
        generated[item.condition_id] += item.output_units
        inputs += item.input_units
    if (any(value > plan.budgets.max_generated_units_per_condition for value in generated.values())
            or sum(generated.values()) > plan.budgets.max_total_generated_units
            or inputs > plan.budgets.max_total_input_units
            or sum(item.elapsed_seconds for item in observed.values()) > plan.budgets.max_total_wall_seconds):
        raise ValueError("fixture evidence exceeds the frozen comparison budget")
    return observed


def _interval(effects: list[float]) -> dict[str, Any]:
    mean, lower, upper = paired_confidence_interval(effects, 1.959963984540054)
    return {"mean": mean, "lower": lower, "upper": upper, "n_problem_blocks": len(effects),
            "scope": "descriptive_fixture_paired_t", "simultaneous_coverage_claimed": False}


def evaluate_comparison_fixture(
    plan: ComparisonPlan, evidence: FixtureEvidence, scorer: ComparisonScorer,
) -> ComparisonEvaluation:
    """Validate all cells before invoking a scorer, then compare common problems."""
    # Revalidate library callers too: model_copy/model_construct and nested
    # mutable dictionaries must not bypass the file loader's strict contract.
    plan = ComparisonPlan.model_validate_json(json.dumps(plan.to_dict(), allow_nan=False))
    evidence = FixtureEvidence.model_validate_json(json.dumps(evidence.to_dict(), allow_nan=False))
    observed = _validate_evidence(plan, evidence)
    if scorer.execution_kind != "fixture" or scorer.lock_digest != plan.protocol.scorer_digest:
        raise ValueError("comparison fixtures require the exact declared fixture scorer")
    cases = {(case.dataset, case.problem.problem_id): case.problem for case in plan.cases}
    datasets = sorted({case.dataset for case in plan.cases})
    for dataset in datasets:
        try:
            scorer.validate_cohort(dataset, [problem for (name, _), problem in cases.items() if name == dataset])
        except ScorerInfrastructureError:
            raise
        except Exception as exc:
            raise ScorerInfrastructureError(f"fixture scorer preflight failed: {type(exc).__name__}") from exc
    scored: list[dict[str, Any]] = []
    for cell in plan.cells:
        item = observed[cell.digest]
        try:
            score = scorer.score_sample(cell.dataset, cases[(cell.dataset, cell.problem_id)], item)
        except ScorerInfrastructureError:
            raise
        except Exception as exc:
            raise ScorerInfrastructureError(f"fixture scorer failed: {type(exc).__name__}") from exc
        if type(score) is not int or score not in (0, 1):
            raise ScorerInfrastructureError("fixture scorer must return integer zero or one")
        scored.append({"condition_id": cell.condition_id, "dataset": cell.dataset,
                       "problem_id": cell.problem_id, "sample_index": cell.sample_index, "seed": cell.seed,
                       "cell_digest": cell.digest, "response_sha256": item.response_sha256, "score": score,
                       "finish_reason": item.finish_reason, "input_units": item.input_units,
                       "output_units": item.output_units, "declared_elapsed_seconds": item.elapsed_seconds})
    metrics, contrasts, interactions = [], [], []
    for dataset in datasets:
        scores: dict[str, dict[str, float]] = {}
        samples_per_problem = getattr(plan.protocol.samples_per_problem, dataset) * len(plan.protocol.seeds)
        for condition in CONDITIONS:
            rows = [row for row in scored if row["condition_id"] == condition and row["dataset"] == dataset]
            accuracy = grouped_sample_accuracy(
                [ScoredSample(row["problem_id"], f"{row['seed']}:{row['sample_index']}", row["score"]) for row in rows],
                samples_per_problem=samples_per_problem,
            )
            scores[condition] = dict(accuracy.problem_scores)
            generated = sorted(row["output_units"] for row in rows)
            metrics.append({
                "condition_id": condition, "dataset": dataset, "mean_sample_accuracy": accuracy.mean_accuracy,
                "n_problems": accuracy.n_problems, "n_samples": accuracy.n_samples,
                "samples_per_problem_across_seeds": samples_per_problem, "problem_scores": scores[condition],
                "completion_rate": statistics.fmean(row["finish_reason"] == "stop" for row in rows),
                "token_limit_rate": statistics.fmean(row["finish_reason"] == "length" for row in rows),
                "declared_input_units": sum(row["input_units"] for row in rows),
                "declared_generated_units": sum(generated), "mean_generated_units": statistics.fmean(generated),
                "p95_generated_units": generated[max(0, math.ceil(0.95 * len(generated)) - 1)],
                "declared_worker_seconds": sum(row["declared_elapsed_seconds"] for row in rows),
            })
        keys = sorted(scores["A"])
        for name, candidate, baseline in CONTRASTS:
            effects = [scores[candidate][key] - scores[baseline][key] for key in keys]
            contrasts.append({"contrast": name, "dataset": dataset, **_interval(effects),
                              "problem_effects": dict(zip(keys, effects, strict=True))})
        effects = [scores["CW"][key] - scores["C"][key] - scores["W"][key] + scores["A"][key] for key in keys]
        interactions.append({"contrast": "CW-C-W+A", "dataset": dataset, **_interval(effects),
                             "problem_effects": dict(zip(keys, effects, strict=True))})
    return ComparisonEvaluation({
        "schema_version": 1, "evidence_kind": "offline_comparison_fixture", "execution_kind": "fixture",
        "model_execution_performed": False, "gpu_execution_deferred": True,
        "benchmark_result_claimed": False, "promotion_claimed": False, "retained_learning_claimed": False,
        "plan_digest": plan.digest, "evidence_digest": evidence.digest,
        "protocol_digest": canonical_digest(plan.protocol.to_dict()), "scorer": scorer.provenance(),
        "conditions": [condition.to_dict() for condition in plan.conditions],
        "artifact_provenance": {
            name: {"artifact_id": artifact.artifact_id, "artifact_digest": artifact.digest,
                   "content_digest": artifact.content_digest, "source": artifact.source.to_dict()}
            for name in ("base_checkpoint", "edited_checkpoint", "base_context", "learned_context",
                         "base_decoding", "selected_decoding")
            for artifact in (getattr(plan.artifacts, name),)
        },
        "declared_contrasts": [contrast.to_dict() for contrast in plan.contrasts],
        "resource_plan": plan.resource_summary(),
        "accounting_units": "synthetic_utf8_bytes_not_model_tokens", "metrics": metrics,
        "contrasts": contrasts, "factorial_interactions": interactions, "samples": scored,
        "notes": [
            "Responses are prerecorded fixtures, not model output; synthetic accuracy is not a benchmark measurement.",
            "Samples and seeds are averaged within each problem; intervals do not measure reliability across editor runs.",
            "Paired t intervals are separate descriptive calculations, not simultaneous guarantees or promotion evidence.",
            "Small cohorts and zero-variance fixture effects do not establish generalization or noninferiority.",
            "Completion rate counts stop termination, including missing or incorrect answers; it is distinct from accuracy.",
            "Byte counts and elapsed values are declared fixture accounting; no inference or optimization cost was measured.",
            "CW attributes the sum of C and W search costs to reused artifacts; it is not a separately optimized candidate.",
        ],
    })


def render_comparison_report(evaluation: ComparisonEvaluation) -> str:
    data = evaluation.to_dict()
    lines = ["# Offline comparison fixture report", "",
             "**Synthetic fixtures only. No model/GPU execution, benchmark result or promotion is claimed.**", "",
             f"Plan digest: `{data['plan_digest']}`.", f"Report digest: `{evaluation.digest}`.", "",
             "| Dataset | Condition | Problems | Samples | Fixture accuracy | Completion rate | Output byte units |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in data["metrics"]:
        lines.append(f"| {row['dataset']} | {row['condition_id']} | {row['n_problems']} | {row['n_samples']} | "
                     f"{row['mean_sample_accuracy']:.3f} | {row['completion_rate']:.3f} | {row['declared_generated_units']} |")
    lines.extend(["", "| Condition | Declared fixture search calls | Declared fixture search units | Attribution |",
                  "| --- | ---: | ---: | --- |"])
    for row in data["conditions"]:
        lines.append(f"| {row['condition_id']} | {row['declared_search_calls']} | {row['declared_search_units']} | "
                     f"{row['search_attribution']} |")
    lines.extend(["", "The JSON includes all checkpoint/context/decoding content identities and fixture source provenance.",
                  f"Unique declared fixture search calls: {data['resource_plan']['declared_unique_search_calls']}; "
                  f"units: {data['resource_plan']['declared_unique_search_units']}. CW does not add another discovery charge."])
    lines.extend(["", "| Dataset | Declared contrast | Mean fixture change | Descriptive 95% interval | Problem blocks |",
                  "| --- | --- | ---: | --- | ---: |"])
    for row in (*data["contrasts"], *data["factorial_interactions"]):
        bounds = "unavailable" if row["lower"] is None else f"[{row['lower']:.3f}, {row['upper']:.3f}]"
        lines.append(f"| {row['dataset']} | {row['contrast']} | {row['mean']:.3f} | {bounds} | {row['n_problem_blocks']} |")
    lines.extend(["", *[f"- {note}" for note in data["notes"]], ""])
    return "\n".join(lines)


def write_comparison_report(evaluation: ComparisonEvaluation, output_dir: Path) -> dict[str, str]:
    """Publish new deterministic files, with hashes and completion marker last."""
    root = output_dir.absolute()
    if any(path.is_symlink() for path in (root, *root.parents)) or root.exists():
        raise ValueError("comparison report output must be a new directory without symlinks")
    payload = json.dumps(evaluation.to_dict(), sort_keys=True, indent=2, allow_nan=False) + "\n"
    markdown = render_comparison_report(evaluation)
    root.mkdir(parents=True)
    write_text_atomic(root / "report.json", payload)
    write_text_atomic(root / "report.md", markdown)
    complete = {"schema_version": 1, "plan_digest": evaluation.payload["plan_digest"],
                "report_digest": evaluation.digest, "evidence_kind": "offline_comparison_fixture",
                "json_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                "markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest()}
    write_text_atomic(root / "complete.json", json.dumps(complete, sort_keys=True, indent=2) + "\n")
    return {"report_digest": evaluation.digest, "json": str(root / "report.json"), "markdown": str(root / "report.md")}
