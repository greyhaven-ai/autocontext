"""Offline AC-1019 integration demo. Replays a fixture synthesis response.

The training traces and candidate evaluations execute through PolicyExecutor;
the provider response is deterministic test data, not a new model experiment.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from autocontext.context_bundles.models import ContextBundle
from autocontext.context_bundles.store import ContextBundleStore
from autocontext.execution.policy_candidate_data import CandidateCase, trace_from_grid_match
from autocontext.execution.policy_candidate_runtime import evaluator_identity, invoke_candidate
from autocontext.execution.policy_candidates import inspect_grid_candidate, synthesize_grid_candidate
from autocontext.execution.policy_executor import PolicyExecutor
from autocontext.providers.base import CompletionResult, LLMProvider
from autocontext.scenarios.grid_ctf import GridCtfScenario

TRAINING_POLICY = """def choose_action(state):
    return {"aggression": 0.9, "defense": 0.5, "path_bias": 1.0}
"""
COUNTEREXAMPLE_POLICY = """def choose_action(state):
    return {"aggression": 0.1, "defense": 0.1, "path_bias": 0.1}
"""
SYNTHESIS_FIXTURE = """def propose_action(state: dict) -> dict:
    return {"status":"act", "action":{"aggression":0.92,"defense":0.48,"path_bias":1.0}}
"""


class FixtureProvider(LLMProvider):
    def default_model(self) -> str:
        return "offline-fixture-v1"

    def complete(self, **kwargs: Any) -> CompletionResult:
        return CompletionResult(text=SYNTHESIS_FIXTURE, model=self.default_model(), stop_reason="stop")


def run(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    store = ContextBundleStore(output / "knowledge")
    # Explicit empty baseline only in this new, isolated demo directory.
    store.bootstrap(ContextBundle.create(scenario="grid_ctf", evaluator_epoch=evaluator_identity(), components=[]))
    active_before = store.active_pointer("grid_ctf")
    executor = PolicyExecutor(GridCtfScenario(), timeout_per_match=2.0, max_moves_per_match=1)
    pool = []
    raw_traces = []
    for seed in range(3):
        source = COUNTEREXAMPLE_POLICY if seed == 2 else TRAINING_POLICY
        row = {"seed": seed, "split": "train", "scenario": "grid_ctf", **asdict(executor.execute_match(source, seed))}
        raw_traces.append(row)
        pool.append(trace_from_grid_match(row, trace_id=f"demo-{seed}", group_id=f"seed-{seed}", split="train", seed=seed))
    (output / "selected-training-traces.json").write_text(json.dumps(raw_traces, indent=2) + "\n")
    cases = [CandidateCase.grid(seed) for seed in range(3001, 3005)] + [
        CandidateCase.grid(seed, shifted=True) for seed in (4001, 4002)
    ]
    result = synthesize_grid_candidate(
        provider=FixtureProvider(),
        provider_name="offline-fixture",
        store=store,
        run_id="ac1019-offline-demo",
        pool=pool,
        selected_ids=["demo-0", "demo-1"],
        counterexample_ids=["demo-2"],
        cases=cases,
    )
    loaded = inspect_grid_candidate(store, result.bundle_digest)
    action = invoke_candidate(loaded.manifest, cases[0].observation_json, expected_digest=loaded.manifest.digest)
    summary = {
        "synthesis": "deterministic fixture; no live model inference",
        "runtime_model_calls": 0,
        "bundle_digest": result.bundle_digest,
        "manifest_digest": result.manifest.digest,
        "lifecycle": result.lifecycle.value,
        "cases": len(result.evaluations),
        "passed": sum(e.passed for e in result.evaluations),
        "fresh_scores": [e.score for e in result.evaluations if e.lane == "fresh"],
        "counterexamples": sum(e.lane == "counterexample" for e in result.evaluations),
        "active_pointer_unchanged": active_before == store.active_pointer("grid_ctf"),
        "replay_decision": action.model_dump(),
        "rejection_reasons": result.rejection_reasons,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory for this isolated demo")
    print(json.dumps(run(parser.parse_args().output), indent=2))
