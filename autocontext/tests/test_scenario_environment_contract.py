from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

ROOT = Path(__file__).parents[2]
MODULE_PATH = ROOT / "autocontext" / "src" / "autocontext" / "scenarios" / "environment_contract.py"
HOOK_KINDS = [
    "setup",
    "reset",
    "rollout",
    "verification",
    "scoring",
    "replay",
    "evidence",
    "cleanup",
]


def _contract_module() -> Any:
    spec = importlib.util.spec_from_file_location("environment_contract", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_docs_schema_matches_python_hook_vocabulary() -> None:
    module = _contract_module()
    schema = json.loads((ROOT / "docs" / "scenario-environment-contract.json").read_text())

    assert schema["required"] == ["schema_version", "scenario_name", "scenario_family", "hooks"]
    assert schema["properties"]["hooks"]["required"] == HOOK_KINDS
    assert schema["$defs"]["hookKind"]["enum"] == list(get_args(module.ScenarioEnvironmentHookKind))


def test_game_scenario_reports_uniform_environment_contract() -> None:
    module = _contract_module()
    contract = module.scenario_environment_contract_for_game(SimpleNamespace(name="grid_ctf"))

    assert contract.scenario_name == "grid_ctf"
    assert contract.scenario_family == "game"
    assert [hook.kind for hook in contract.hooks.reset]
    assert [hook.kind for hook in contract.hooks.rollout]
    assert [hook.kind for hook in contract.hooks.verification]
    assert contract.hooks.scoring[0].emits == ["scalar_score"]
    assert contract.hooks.replay[0].emits == ["replay_timeline"]

    dumped = contract.model_dump(mode="json")
    reparsed = module.ScenarioEnvironmentContract.model_validate(dumped)
    assert reparsed.model_dump(mode="json") == dumped


def test_template_exposes_environment_contract() -> None:
    spec_yaml = (
        ROOT
        / "autocontext"
        / "src"
        / "autocontext"
        / "scenarios"
        / "templates"
        / "content-generation"
        / "spec.yaml"
    ).read_text()

    assert "environment_contract:" in spec_yaml
    assert "scenario_family: agent_task" in spec_yaml
    assert "kind: verification" in spec_yaml
    assert "judge_reasoning" in spec_yaml
    assert "dimension_scores" in spec_yaml
