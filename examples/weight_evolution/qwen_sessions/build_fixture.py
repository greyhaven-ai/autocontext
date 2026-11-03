"""Freeze literal CPU editing programs against the existing M5 toy comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.comparison_io import load_comparison_fixture
from autocontext.weight_evolution.comparison_models import ComparisonBudgets, ComparisonCase
from autocontext.weight_evolution.datasets import DatasetInstruction, EvaluationProblem
from autocontext.weight_evolution.session_data import SessionDataBundle
from autocontext.weight_evolution.session_fixtures import ScriptedSessionAdapter, SessionFixtureEntry, SessionFixtureScript
from autocontext.weight_evolution.session_models import (
    EditOperation,
    InspectOperation,
    Mechanism,
    ProbeOperation,
    ProbeOutput,
    SessionBaseline,
    SessionDecoding,
    SessionLimits,
    SessionSpec,
    SessionUsage,
    apply_edit,
    initial_candidate,
)
from autocontext.weight_evolution.session_workflow import FixtureSessionProgram, SessionWorkflow, SessionWorkflowLock

COMPARISON = Path(__file__).resolve().parents[1] / "qwen_comparison" / "fixtures"


def build_fixture(output: Path, comparison_directory: Path = COMPARISON) -> None:
    if output.exists() or any(path.is_symlink() for path in (output.absolute(), *output.absolute().parents)):
        raise ValueError("fixture output must be a new directory without symlinks")
    source, evidence = load_comparison_fixture(comparison_directory)
    data = SessionDataBundle(
        practice=source.development.cases, final_audit=source.cases,
        confirmation=(
            ComparisonCase(dataset="math500", problem=EvaluationProblem(
                problem_id="confirmation.sum", text="What is eight plus six?", answer="14")),
            ComparisonCase(dataset="gsm8k", problem=EvaluationProblem(
                problem_id="confirmation.coins", text="Paz has eleven coins and gives away four. How many remain?",
                answer="7")),
            ComparisonCase(dataset="ifeval", problem=EvaluationProblem(
                problem_id="confirmation.words", text="Reply in exactly two words including coral.",
                instructions=(DatasetInstruction(instruction_id="fixture:word_count", kwargs={"count": 2}),
                              DatasetInstruction(instruction_id="fixture:contains_word", kwargs={"word": "coral"})))),
        ),
    )
    baseline = SessionBaseline(base_checkpoint=source.artifacts.base_checkpoint,
                               base_context=source.artifacts.base_context, base_decoding=source.artifacts.base_decoding)
    reserve = SessionUsage(operations=1, editor_input_bytes=16384, editor_output_bytes=4096, simulated_seconds=1.0)
    probe_reserve = SessionUsage(**{**reserve.to_dict(), "probe_input_bytes": 4096, "probe_output_bytes": 4096})
    limits = SessionLimits(max_operations=4, max_editor_input_bytes=65536, max_editor_output_bytes=16384,
                           max_probe_input_bytes=8192, max_probe_output_bytes=8192, max_simulated_seconds=4.0)
    practice_ids = tuple(case.problem.problem_id for case in data.practice)
    # Literal prerecorded responses: neither the builder nor adapter solves questions.
    responses = {"C": (r"\boxed{3}", r"\boxed{9}", "</think>green leaf"),
                 "W": (r"\boxed{3}", "", "</think>green leaf"),
                 "D": (r"\boxed{4}", r"\boxed{9}", "</think>green leaf")}
    programs = []
    mechanisms: tuple[Mechanism, ...] = ("C", "W", "D")
    for mechanism in mechanisms:
        provisional = SessionSpec(session_id=f"fixture-session-{mechanism.lower()}", mechanism=mechanism,
                                  baseline=baseline, data_digest=data.digest,
                                  scorer_digest=SyntheticComparisonScorer().lock_digest,
                                  adapter_digest="0" * 64, limits=limits)
        initial = initial_candidate(provisional)
        inspect = InspectOperation(operation_id="inspect-practice", candidate_digest=initial.digest,
                                   reservation=reserve, target="practice", problem_ids=practice_ids)
        edit = EditOperation(
            operation_id="edit-candidate", candidate_digest=initial.digest, reservation=reserve,
            context_text=source.artifacts.learned_context.text if mechanism == "C" else None,
            weight_descriptor=source.artifacts.edited_checkpoint.content if mechanism == "W" else None,
            decoding=SessionDecoding.model_validate(source.artifacts.selected_decoding.settings())
            if mechanism == "D" else None,
        )
        edited = apply_edit(provisional, initial, edit)
        failed = ProbeOperation(operation_id="failed-probe", candidate_digest=edited.digest,
                                reservation=probe_reserve, problem_ids=practice_ids)
        probe = ProbeOperation(operation_id="practice-probe", candidate_digest=edited.digest,
                               reservation=probe_reserve, problem_ids=practice_ids)
        operations = (inspect, edit, failed, probe)
        entries = []
        for operation in operations:
            outputs = tuple(ProbeOutput(problem_id=key, text=text)
                            for key, text in zip(practice_ids, responses[mechanism], strict=True))
            entries.append(SessionFixtureEntry(
                operation_digest=operation.digest, candidate_digest=operation.candidate_digest,
                operation_kind=operation.kind, outcome="failure" if operation == failed else "return",
                message="scripted known failure" if operation == failed else "literal offline fixture response",
                probe_outputs=outputs if operation == probe else (), simulated_seconds=0.01,
            ))
        script = SessionFixtureScript(entries=tuple(entries))
        spec = SessionSpec.model_validate_json(json.dumps({**provisional.to_dict(),
                                                          "adapter_digest": ScriptedSessionAdapter(script).identity_digest}))
        programs.append(FixtureSessionProgram(spec=spec, operations=operations, script=script))
    budgets = ComparisonBudgets.model_validate_json(json.dumps({**source.budgets.to_dict(),
                                                                "max_search_calls_per_mechanism": 4,
                                                                "max_search_units_per_mechanism": 100000}))
    workflow = SessionWorkflow(workflow_id="qwen-editing-sessions-synthetic-v1", data_digest=data.digest,
                               source_comparison_plan_digest=source.digest,
                               source_comparison_evidence_digest=evidence.digest, budgets=budgets,
                               programs=tuple(programs))
    data_bytes = (json.dumps(data.to_dict(), sort_keys=True, indent=2) + "\n").encode()
    workflow_bytes = (json.dumps(workflow.to_dict(), sort_keys=True, indent=2) + "\n").encode()
    lock = SessionWorkflowLock(data_sha256=hashlib.sha256(data_bytes).hexdigest(), data_size_bytes=len(data_bytes),
                               workflow_sha256=hashlib.sha256(workflow_bytes).hexdigest(),
                               workflow_size_bytes=len(workflow_bytes))
    output.mkdir(parents=True)
    (output / "data.json").write_bytes(data_bytes)
    (output / "workflow.json").write_bytes(workflow_bytes)
    (output / "fixtures.lock.json").write_text(json.dumps(lock.to_dict(), sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comparison-fixtures", type=Path, default=COMPARISON)
    args = parser.parse_args()
    build_fixture(args.output, args.comparison_fixtures)
