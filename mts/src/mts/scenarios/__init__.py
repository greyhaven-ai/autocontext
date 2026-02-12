from typing import TypeAlias

from mts.scenarios.base import ScenarioInterface
from mts.scenarios.grid_ctf import GridCtfScenario
from mts.scenarios.othello import OthelloScenario

ScenarioFactory: TypeAlias = type[ScenarioInterface]

SCENARIO_REGISTRY: dict[str, ScenarioFactory] = {
    "grid_ctf": GridCtfScenario,
    "othello": OthelloScenario,
}
