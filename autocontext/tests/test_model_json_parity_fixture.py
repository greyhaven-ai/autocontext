"""AC-937: the shared parity fixture must reflect what Python actually does.

`docs/model-json-extraction-parity-fixtures.json` is the contract the TypeScript
port replays. It is GENERATED from `extract_json`, so it is only worth anything
while it stays in sync with the function it was generated from.

Without this gate the failure is silent and backwards: a change to the Python
parser leaves a stale fixture behind, TypeScript keeps replaying the OLD
behavior and keeps passing, and the two engines drift while every suite stays
green. That is the same shape as the divergence AC-937 exists to close.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO.parent / "docs" / "model-json-extraction-parity-fixtures.json"
GENERATOR_PATH = REPO / "scripts" / "generate_model_json_parity_fixtures.py"


def _generator():
    spec = importlib.util.spec_from_file_location("_ac937_generator", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fixture_matches_what_the_python_parser_produces() -> None:
    """Regenerate in memory and compare. Stale fixture fails here, not in CI later."""
    generated = _generator().build()
    on_disk = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert on_disk == generated, (
        "docs/model-json-extraction-parity-fixtures.json is stale. Regenerate with:\n"
        "  uv run --frozen python scripts/generate_model_json_parity_fixtures.py"
    )


def test_fixture_covers_every_rule_group() -> None:
    """The groups are the rules. Losing one would quietly narrow the contract.

    Exact-set equality rather than a subset check, so deleting a group fails
    just as loudly as adding an untracked one.
    """
    on_disk = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert set(on_disk["groups"]) == {
        "direct",
        "fence_selection",
        "recovery",
        "arrays",
        "bom",
        "require_unique",
        "failure_policy",
    }


@pytest.mark.parametrize("group", ["fence_selection", "arrays", "recovery"])
def test_the_load_bearing_groups_are_not_empty(group: str) -> None:
    """These three carry the rules that were regressions before.

    A fixture that still parsed but had been emptied of the cases that matter
    would keep every replay green while testing nothing.
    """
    on_disk = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert len(on_disk["groups"][group]) >= 5
