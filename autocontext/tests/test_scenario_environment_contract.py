from __future__ import annotations

import importlib
import importlib.util
import json
import sys
from copy import deepcopy
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
    for kind in HOOK_KINDS:
        assert schema["properties"]["hooks"]["properties"][kind]["$ref"] == f"#/$defs/{kind}HookList"
        assert schema["$defs"][f"{kind}Hook"]["allOf"][1]["properties"]["kind"]["const"] == kind


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
    templates = importlib.import_module("autocontext.scenarios.templates")
    spec = templates.TemplateLoader().get_template("content-generation")

    assert spec.environment_contract is not None
    assert spec.environment_contract.scenario_family == "agent_task"
    assert spec.environment_contract.hooks.verification[0].kind == "verification"
    assert spec.environment_contract.hooks.evidence[0].emits == ["judge_reasoning", "dimension_scores"]


def test_python_contract_rejects_schema_invalid_data() -> None:
    module = _contract_module()
    valid = module.agent_task_template_environment_contract("content-generation").model_dump(mode="json")
    invalid_contracts = []

    missing_required = deepcopy(valid)
    del missing_required["hooks"]["setup"][0]["required"]
    invalid_contracts.append(missing_required)

    empty_group = deepcopy(valid)
    empty_group["hooks"]["setup"] = []
    invalid_contracts.append(empty_group)

    wrong_kind = deepcopy(valid)
    wrong_kind["hooks"]["setup"][0]["kind"] = "cleanup"
    invalid_contracts.append(wrong_kind)

    coerced_list_item = deepcopy(valid)
    coerced_list_item["hooks"]["setup"][0]["emits"] = [123]
    invalid_contracts.append(coerced_list_item)

    for contract in invalid_contracts:
        try:
            module.ScenarioEnvironmentContract.model_validate(contract)
        except (KeyError, TypeError, ValueError):
            continue
        raise AssertionError("schema-invalid environment contract was accepted")
