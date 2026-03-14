from pathlib import Path
from typing import Any, TypeAlias

from autocontext.scenarios.families import ScenarioFamily, detect_family
from autocontext.scenarios.grid_ctf import GridCtfScenario
from autocontext.scenarios.othello import OthelloScenario

ScenarioFactory: TypeAlias = type[Any]

SCENARIO_REGISTRY: dict[str, ScenarioFactory] = {
    "grid_ctf": GridCtfScenario,
    "othello": OthelloScenario,
}


def _load_persisted_custom_scenarios() -> None:
    from autocontext.scenarios.custom.registry import load_all_custom_scenarios

    knowledge_root = Path("knowledge")
    if knowledge_root.is_dir():
        custom = load_all_custom_scenarios(knowledge_root)
        SCENARIO_REGISTRY.update(custom)


_load_persisted_custom_scenarios()


def get_registered_scenario_family(name: str) -> ScenarioFamily:
    """Return the registered family metadata for a scenario name."""
    cls = SCENARIO_REGISTRY[name]
    family = detect_family(cls())
    if family is None:
        raise TypeError(f"Unable to determine scenario family for '{name}'")
    return family
