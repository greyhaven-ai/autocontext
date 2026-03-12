from pathlib import Path
from typing import TypeAlias

from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.grid_ctf import GridCtfScenario
from autocontext.scenarios.othello import OthelloScenario

ScenarioFactory: TypeAlias = type[ScenarioInterface]

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
