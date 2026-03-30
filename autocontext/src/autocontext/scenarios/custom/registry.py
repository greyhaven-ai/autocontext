from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path
from typing import Any

from autocontext.scenarios.custom.agent_task_revision import (
    patch_legacy_generated_evaluate_output,
    patch_legacy_generated_revise_output,
)
from autocontext.scenarios.custom.loader import load_custom_scenario
from autocontext.scenarios.families import detect_family, get_family_by_marker

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
    spec.loader.exec_module(mod)

    for attr_name in dir(mod):
        attr = getattr(mod, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, AgentTaskInterface)
            and attr is not AgentTaskInterface
        ):
            attr = patch_legacy_generated_evaluate_output(attr, source_path)
            return patch_legacy_generated_revise_output(attr, source_path)

    raise ImportError(f"no AgentTaskInterface subclass found in {module_name}")


def _load_family_class(custom_dir: Path, name: str, marker: str) -> type[Any]:
    family = get_family_by_marker(marker)

    if family.name == "agent_task":
        agent_task_file = custom_dir / name / "agent_task.py"
        if not agent_task_file.exists():
            raise FileNotFoundError(f"agent task source not found: {agent_task_file}")
        return _load_agent_task_class(custom_dir, name)

    cls = load_custom_scenario(custom_dir, name, family.interface_class)
    detected = detect_family(cls())
    if detected is None or detected.name != family.name:
        raise ImportError(
            f"loaded scenario '{name}' as family '{detected.name if detected else 'unknown'}', "
            f"expected '{family.name}'"
        )
    return cls


def load_all_custom_scenarios(knowledge_root: Path) -> dict[str, type[Any]]:
    custom_dir = knowledge_root / CUSTOM_SCENARIOS_DIR
    if not custom_dir.is_dir():
        return {}

    loaded: dict[str, type[Any]] = {}
    for entry in sorted(custom_dir.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name

        type_file = entry / "scenario_type.txt"
        marker = type_file.read_text().strip() if type_file.exists() else "parametric"
        try:
            cls = _load_family_class(custom_dir, name, marker)
            loaded[name] = cls
        except FileNotFoundError:
            continue
        except KeyError:
            logger.warning("failed to load custom scenario '%s': unknown marker '%s'", name, marker)
        except Exception:
            logger.warning("failed to load custom scenario '%s'", name, exc_info=True)

    return loaded
