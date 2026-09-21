from __future__ import annotations

import json
from pathlib import Path

from run import PilotScenario, episode, summarize

from autocontext.providers.base import CompletionResult

PROTOCOL = json.loads((Path(__file__).parent / "protocol.json").read_text())
POLICY = "def choose_action(state):\n    return {'aggression': 1.0, 'defense': 0.4, 'path_bias': 1.0}"


class StubProvider:
    def __init__(self) -> None:
        self.calls = []
        self.protocol = dict(PROTOCOL)

    def complete(self, *args):
        self.calls.append({"total_tokens": 10})
        return CompletionResult(text='{"aggression": 0.8, "defense": 0.0, "path_bias": 1.0}')


def test_changed_contract_invalidates_before_policy_execution_and_hybrid_falls_back():
    provider = StubProvider()
    pure = episode("policy", 201, PilotScenario(True), "raise RuntimeError('must not run')", provider, PROTOCOL)
    assert pure["invalidated"]
    assert pure["model_calls"] == 0
    assert not pure["errors"]
    hybrid = episode("hybrid", 201, PilotScenario(True), "raise RuntimeError('must not run')", provider, PROTOCOL)
    assert hybrid["abstained"] and hybrid["success"]
    assert hybrid["model_calls"] == 1
    assert pure["state_sha256"] == hybrid["state_sha256"]


def test_supported_policy_uses_existing_isolated_executor_without_model_calls():
    provider = StubProvider()
    row = episode("policy", 101, PilotScenario(), POLICY, provider, PROTOCOL)
    assert row["success"] and not row["errors"]
    assert row["model_calls"] == 0
    assert row["replay"]["replay"][-1]["action"]["aggression"] == 1.0


def test_cost_accounting_includes_setup_failed_candidates_and_failed_episodes():
    rows = []
    for split, seed in (("heldout", 101), ("shifted", 201)):
        for arm in ("model", "policy", "hybrid"):
            rows.append({"split": split, "seed": seed, "arm": arm, "score": .9,
                         "success": not (split == "shifted" and arm == "policy"),
                         "seconds": 5 if arm == "model" else .1,
                         "model_calls": 1 if arm == "model" else 0,
                         "tokens": 20 if arm == "model" else 0,
                         "errors": [], "illegal_actions": 0,
                         "abstained": split == "shifted" and arm == "hybrid",
                         "invalidated": split == "shifted" and arm == "policy"})
    calls = [{"phase": "synthesis", "total_tokens": 40, "usage_known": True, "status": "failed"},
             {"phase": "synthesis", "total_tokens": 60, "usage_known": True, "status": "completed"}]
    result = summarize(rows, [{}, {}], calls, 12.0, PROTOCOL)
    assert result["setup"]["tokens"] == 100
    assert result["actual_resources_per_success_with_setup"]["policy"]["tokens_per_success"] == 100
    assert result["actual_resources_per_success_with_setup"]["policy"]["seconds_per_success"] == 12.2
    assert result["projected_horizons"]["model"][0]["tokens"] == 20
    assert result["projected_horizons"]["policy"][0]["tokens"] == 100
    assert result["projected_break_even_episodes"]["policy"]["tokens"] == 6
    assert result["observed_first_resource_crossing_episodes"]["policy"]["tokens"] is None
