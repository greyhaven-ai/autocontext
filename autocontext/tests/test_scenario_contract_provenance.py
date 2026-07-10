"""ERP-73 — scenario-contract provenance for structural role isolation.

The scenario contract (rules / interface / criteria) may hold system authority
ONLY for first-party, code-authored built-in scenarios. `is_operator_authored_
scenario` positively identifies those, so everything else — solve/codegen-
generated, third-party / consumer-repo, `__main__`, dynamically loaded, or
unknown — is fail-safe untrusted.
"""

from __future__ import annotations

import autocontext.agents  # noqa: F401  # break circular import with prompts.templates
from autocontext.prompts.templates import PromptPartsBundle, build_prompt_bundle
from autocontext.scenarios.base import Observation
from autocontext.scenarios.custom.loader import is_operator_authored_scenario

_CONTRACT_SENTINEL = "CONTRACT-RULES-SENTINEL-42"


def _scenario_in_module(module: str) -> object:
    cls = type("Scenario", (), {})
    cls.__module__ = module
    return cls()


def _competitor_parts_for(scenario: object):  # type: ignore[no-untyped-def]
    """Drive the real provenance → placement path: helper decides trust, then
    build_prompt_bundle places the contract accordingly."""
    captured: dict[str, PromptPartsBundle] = {}
    build_prompt_bundle(
        scenario_rules=f"rules {_CONTRACT_SENTINEL}",
        strategy_interface="interface",
        evaluation_criteria="criteria",
        previous_summary="summary",
        observation=Observation(narrative="obs", state={}, constraints=[]),
        current_playbook="playbook",
        available_tools="tools",
        scenario_contract_trusted=is_operator_authored_scenario(scenario),
        prompt_parts_sink=lambda parts: captured.__setitem__("parts", parts),
    )
    return captured["parts"].competitor


def test_builtin_first_party_scenario_is_operator_authored() -> None:
    assert is_operator_authored_scenario(_scenario_in_module("autocontext.scenarios.grid_ctf")) is True


def test_generated_package_scenario_is_not_operator_authored() -> None:
    assert is_operator_authored_scenario(_scenario_in_module("autocontext.scenarios.custom.generated.some_task")) is False


def test_generated_agent_task_scenario_is_not_operator_authored() -> None:
    assert is_operator_authored_scenario(_scenario_in_module("autocontext.scenarios.custom.generated.agent_task_foo")) is False


def test_custom_but_non_generated_module_is_not_operator_authored() -> None:
    # Anything under scenarios.custom is treated as untrusted (fail-safe).
    assert is_operator_authored_scenario(_scenario_in_module("autocontext.scenarios.custom.registry")) is False


def test_third_party_scenario_is_not_operator_authored() -> None:
    # The reviewer's repro: a consumer-repo / third-party module must NOT be trusted.
    assert is_operator_authored_scenario(_scenario_in_module("third_party.llm_generated")) is False


def test_main_module_scenario_is_not_operator_authored() -> None:
    assert is_operator_authored_scenario(_scenario_in_module("__main__")) is False


def test_object_without_scenario_module_is_not_operator_authored() -> None:
    assert is_operator_authored_scenario(object()) is False


# End-to-end: provenance actually drives contract placement (not just the helper).


def test_builtin_scenario_contract_lands_in_the_system_turn() -> None:
    competitor = _competitor_parts_for(_scenario_in_module("autocontext.scenarios.grid_ctf"))
    assert _CONTRACT_SENTINEL in competitor.system
    assert _CONTRACT_SENTINEL not in competitor.untrusted_reference


def test_third_party_scenario_contract_lands_in_the_untrusted_turn() -> None:
    competitor = _competitor_parts_for(_scenario_in_module("third_party.llm_generated"))
    assert _CONTRACT_SENTINEL in competitor.untrusted_reference
    assert _CONTRACT_SENTINEL not in competitor.system


def test_generated_scenario_contract_lands_in_the_untrusted_turn() -> None:
    competitor = _competitor_parts_for(_scenario_in_module("autocontext.scenarios.custom.generated.x"))
    assert _CONTRACT_SENTINEL in competitor.untrusted_reference
    assert _CONTRACT_SENTINEL not in competitor.system
