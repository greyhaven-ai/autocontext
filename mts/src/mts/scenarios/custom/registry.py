from __future__ import annotations

import logging
from pathlib import Path

from mts.scenarios.base import ScenarioInterface
from mts.scenarios.custom.loader import load_custom_scenario

logger = logging.getLogger(__name__)

CUSTOM_SCENARIOS_DIR = "_custom_scenarios"


def load_all_custom_scenarios(knowledge_root: Path) -> dict[str, type[ScenarioInterface]]:
    custom_dir = knowledge_root / CUSTOM_SCENARIOS_DIR
    if not custom_dir.is_dir():
        return {}

    loaded: dict[str, type[ScenarioInterface]] = {}
    for entry in sorted(custom_dir.iterdir()):
        if not entry.is_dir():
            continue
        spec_file = entry / "spec.json"
        scenario_file = entry / "scenario.py"
        if not spec_file.exists() or not scenario_file.exists():
            continue
        name = entry.name
        try:
            cls = load_custom_scenario(custom_dir, name)
            loaded[name] = cls
        except Exception:
            logger.warning("failed to load custom scenario '%s'", name, exc_info=True)

    return loaded
