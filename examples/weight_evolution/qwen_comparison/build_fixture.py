"""Freeze hand-authored toy inputs/responses into a new offline fixture bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from autocontext.weight_evolution.comparison_evaluation import FixtureEvidence, fixture_observation
from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.comparison_io import ComparisonFixtureLock
from autocontext.weight_evolution.comparison_models import (
    ArtifactSource,
    CheckpointArtifact,
    ComparisonArtifacts,
    ComparisonBudgets,
    ComparisonCase,
    ComparisonProtocol,
    ComparisonSampleCounts,
    ContextArtifact,
    DecodingArtifact,
    DevelopmentProvenance,
    build_comparison_plan,
)
from autocontext.weight_evolution.datasets import DatasetInstruction, EvaluationProblem


def math_case(dataset: str, key: str, prompt: str, gold: str) -> ComparisonCase:
    return ComparisonCase(dataset=dataset, problem=EvaluationProblem(  # type: ignore[arg-type]
        problem_id=key, text=prompt, answer=gold,
    ))


def instruction_case(key: str, prompt: str, *, word: str | None = None, count: int, uppercase: bool = False) -> ComparisonCase:
    instructions = [DatasetInstruction(instruction_id="fixture:word_count", kwargs={"count": count})]
    if word:
        instructions.append(DatasetInstruction(instruction_id="fixture:contains_word", kwargs={"word": word}))
    if uppercase:
        instructions.append(DatasetInstruction(instruction_id="fixture:uppercase", kwargs={}))
    return ComparisonCase(dataset="ifeval", problem=EvaluationProblem(
        problem_id=key, text=prompt, instructions=tuple(instructions),
    ))


def build_fixture(output: Path) -> None:
    """Generate no answers: bind the literal response catalog below to the plan."""
    if output.exists() or any(p.is_symlink() for p in (output.absolute(), *output.absolute().parents)):
        raise ValueError("fixture output must be a new directory without symlinks")
    practice = (
        math_case("math500", "practice.sum", "What is 1 plus 2?", "3"),
        math_case("gsm8k", "practice.pears", "Mia has seven pears and buys two. How many pears now?", "9"),
        instruction_case("practice.words", "Reply in two words including green.", word="green", count=2),
    )
    cases = (
        math_case("math500", "audit.sum", "What is 17 plus 25?", "42"),
        math_case("math500", "audit.product", "What is 6 times 7?", "42"),
        math_case("gsm8k", "audit.books", "Iris has nine books and gives away three. How many remain?", "6"),
        math_case("gsm8k", "audit.boxes", "Three boxes each hold five stones. How many stones in total?", "15"),
        instruction_case("audit.violet", "Reply with exactly three words including violet.", word="violet", count=3),
        instruction_case("audit.upper", "Reply in uppercase using exactly two words.", count=2, uppercase=True),
    )
    baseline = ArtifactSource(kind="baseline_fixture")
    sources = [ArtifactSource(kind="development_fixture", source_problem_ids=tuple(c.problem.problem_id for c in practice),
                              declared_search_calls=calls, declared_search_units=calls * 100) for calls in (3, 4, 2)]
    artifacts = ComparisonArtifacts(
        base_checkpoint=CheckpointArtifact(artifact_id="fixture-base-weights", source=baseline,
                                            content="Synthetic base checkpoint descriptor; contains no model weights."),
        edited_checkpoint=CheckpointArtifact(artifact_id="fixture-edited-weights",
                                              content="Synthetic edited checkpoint descriptor; contains no model weights.",
                                              source=sources[1]),
        base_context=ContextArtifact(artifact_id="fixture-base-context", text="", source=baseline),
        learned_context=ContextArtifact(artifact_id="fixture-context-candidate", source=sources[0],
                                         text="Work through each arithmetic step once. "
                                         "Check the requested format before answering."),
        base_decoding=DecodingArtifact(artifact_id="fixture-base-decoding", temperature=0.6, top_p=0.95,
                                        top_k=20, presence_penalty=0.0, source=baseline),
        selected_decoding=DecodingArtifact(artifact_id="fixture-decoding-candidate", temperature=0.2, top_p=0.95,
                                            top_k=20, presence_penalty=1.0, source=sources[2]),
    )
    plan = build_comparison_plan(
        study_id="qwen-comparison-synthetic-v1", cases=cases, artifacts=artifacts,
        development=DevelopmentProvenance(cases=practice),
        protocol=ComparisonProtocol(
            runtime_digest=hashlib.sha256(b"synthetic-no-model-runtime-v1").hexdigest(),
            tokenizer_digest=hashlib.sha256(b"utf8-bytes-v1").hexdigest(),
            scorer_digest=SyntheticComparisonScorer().lock_digest, seeds=(0, 17),
            samples_per_problem=ComparisonSampleCounts(math500=1, gsm8k=1, ifeval=1),
            max_new_tokens=96, math_prompt_suffix="\nReturn the answer in \\boxed{...}.",
        ),
        budgets=ComparisonBudgets(max_input_units_per_sample=2048, max_generated_units_per_condition=1152,
                                  max_total_generated_units=5760, max_total_input_units=100000,
                                  max_total_wall_seconds=10.0, max_search_calls_per_mechanism=5,
                                  max_search_units_per_mechanism=500),
    )
    # This literal catalog is independent of gold/scorer results. It deliberately
    # contains regressions, wrong/empty answers, and cap-length completions.
    box42, box6, box15 = r"\boxed{42}", r"\boxed{6}", r"\boxed{15}"
    violet, upper = "</think>violet sky glows", "</think>HELLO WORLD"
    cap = "?" * 96
    responses = {
        "A": ((r"\boxed{41}", ""), (box42, box42), (box6, r"\boxed{9}"), (cap, cap),
              ("violet sky glows", violet), ("</think>hello world", upper)),
        "C": ((box42, box42.ljust(96)), (r"\boxed{40}", box42), (box6, box6), (r"\boxed{13}", box15),
              (violet, violet), (upper, upper)),
        "W": ((box42, r"\boxed{41}"), (box42, box42), (box6, r"\boxed{9}"), (box15, box15),
              ("violet sky glows", violet), (upper, upper)),
        "CW": ((box42, box42), (box42, box42), (box6, box6), (box15, cap), (violet, violet), (upper, "")),
        "D": ((r"\boxed{41}", box42), (box42, box42), (r"\boxed{9}", box6), (box15, cap),
              (violet, "violet sky glows"), ("</think>hello world", "</think>hello world")),
    }
    case_order = {case.problem.problem_id: index for index, case in enumerate(cases)}
    observations = []
    for cell in plan.cells:
        text = responses[cell.condition_id][case_order[cell.problem_id]][plan.protocol.seeds.index(cell.seed)]
        observations.append(fixture_observation(cell, text, finish_reason="length" if len(text.encode()) == 96 else "stop",
                                                elapsed_seconds=0.01))
    evidence = FixtureEvidence(plan_digest=plan.digest, observations=tuple(observations))
    study_bytes = (json.dumps(plan.to_dict(), sort_keys=True, indent=2) + "\n").encode()
    response_bytes = (json.dumps(evidence.to_dict(), sort_keys=True, indent=2) + "\n").encode()
    lock = ComparisonFixtureLock(study_sha256=hashlib.sha256(study_bytes).hexdigest(), study_size_bytes=len(study_bytes),
                                  responses_sha256=hashlib.sha256(response_bytes).hexdigest(),
                                  responses_size_bytes=len(response_bytes))
    output.mkdir(parents=True)
    (output / "study.json").write_bytes(study_bytes)
    (output / "responses.json").write_bytes(response_bytes)
    (output / "fixtures.lock.json").write_text(json.dumps(lock.to_dict(), sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    build_fixture(parser.parse_args().output)
