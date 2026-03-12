from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.custom.loader import load_custom_scenario

logger = logging.getLogger(__name__)

CUSTOM_SCENARIOS_DIR = "_custom_scenarios"


def _load_agent_task_class(custom_dir: Path, name: str) -> type[Any]:
    """Load an agent task class from custom_dir/name/agent_task.py."""
    from autocontext.scenarios.agent_task import AgentTaskInterface

    module_name = f"autocontext.scenarios.custom.generated.agent_task_{name}"
    source_path = custom_dir / name / "agent_task.py"

    if module_name in sys.modules:
        del sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, str(source_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create module spec for {source_path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, AgentTaskInterface)
            and attr is not AgentTaskInterface
        ):
            return attr

    raise ImportError(f"no AgentTaskInterface subclass found in {module_name}")


def load_all_custom_scenarios(knowledge_root: Path) -> dict[str, type[ScenarioInterface]]:
    custom_dir = knowledge_root / CUSTOM_SCENARIOS_DIR
    if not custom_dir.is_dir():
        return {}

    loaded: dict[str, type[ScenarioInterface]] = {}
    for entry in sorted(custom_dir.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name

        # Check if this is an agent_task scenario
        type_file = entry / "scenario_type.txt"
        if type_file.exists() and type_file.read_text().strip() == "agent_task":
            agent_task_file = entry / "agent_task.py"
            if not agent_task_file.exists():
                continue
            try:
                cls = _load_agent_task_class(custom_dir, name)
                loaded[name] = cls  # type: ignore[assignment]
            except Exception:
                logger.warning("failed to load agent task '%s'", name, exc_info=True)
            continue

        # Standard parametric scenario
        spec_file = entry / "spec.json"
        scenario_file = entry / "scenario.py"
        if not spec_file.exists() or not scenario_file.exists():
            continue
        try:
            cls = load_custom_scenario(custom_dir, name)
            loaded[name] = cls
        except Exception:
            logger.warning("failed to load custom scenario '%s'", name, exc_info=True)

    return loaded
