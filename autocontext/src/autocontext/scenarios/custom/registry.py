from __future__ import annotations

import importlib.util
import json
import logging
import sys
from collections.abc import Mapping
from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class ScenarioLoadError:
    """A single custom-scenario directory that could not be loaded.

    Part of the AC-563 domain model. Emitted by
    :func:`load_custom_scenarios_detailed` so callers can surface skipped
    scenarios in a UI without parsing stderr.
    """

    name: str
    spec_path: Path
    reason: str
    marker: str


@dataclass(frozen=True, slots=True)
class ScenarioRegistryLoadResult:
    """Aggregate result of attempting to load all custom scenarios."""

    loaded: Mapping[str, type[Any]]
    skipped: tuple[ScenarioLoadError, ...]


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
        if isinstance(attr, type) and issubclass(attr, AgentTaskInterface) and attr is not AgentTaskInterface:
            attr = patch_legacy_generated_evaluate_output(attr, source_path)
            return patch_legacy_generated_revise_output(attr, source_path)

    raise ImportError(f"no AgentTaskInterface subclass found in {module_name}")


def _read_persisted_marker(entry: Path) -> str:
    type_file = entry / "scenario_type.txt"
    if type_file.exists():
        return type_file.read_text().strip()

    spec_file = entry / "spec.json"
    if spec_file.exists():
        try:
            raw = json.loads(spec_file.read_text(encoding="utf-8"))
        except Exception:
            return "parametric"
        marker = raw.get("scenario_type") or raw.get("scenarioType")
        if isinstance(marker, str) and marker.strip():
            return marker.strip()

    return "parametric"


def _materialize_parametric_scenario_source(custom_dir: Path, name: str) -> None:
    from autocontext.scenarios.custom.codegen import generate_scenario_class
    from autocontext.scenarios.custom.spec import ScenarioSpec

    scenario_dir = custom_dir / name
    spec = ScenarioSpec.load(scenario_dir)
    if spec.name != name:
        spec.name = name

    source_path = scenario_dir / "scenario.py"
    if not source_path.exists():
        source_path.write_text(generate_scenario_class(spec), encoding="utf-8")

    type_file = scenario_dir / "scenario_type.txt"
    if not type_file.exists():
        type_file.write_text("parametric", encoding="utf-8")


def _load_family_class(custom_dir: Path, name: str, marker: str) -> type[Any]:
    family = get_family_by_marker(marker)

    if family.name == "agent_task":
        agent_task_file = custom_dir / name / "agent_task.py"
        if not agent_task_file.exists():
            raise FileNotFoundError(f"agent task source not found: {agent_task_file}")
        return _load_agent_task_class(custom_dir, name)

    if marker == "parametric":
        _materialize_parametric_scenario_source(custom_dir, name)

    cls = load_custom_scenario(custom_dir, name, family.interface_class)
    detected = detect_family(cls())
    if detected is None or detected.name != family.name:
        raise ImportError(
            f"loaded scenario '{name}' as family '{detected.name if detected else 'unknown'}', expected '{family.name}'"
        )
    return cls


def _summarize_load_failure(exc: BaseException, marker: str) -> str:
    """Render a single-line, user-friendly reason string for a load failure.

    Best-effort: never raises. Falls back to ``str(exc)`` if rendering fails.
    """
    try:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            errors = exc.errors()
            if errors:
                first = errors[0]
                loc = ".".join(str(part) for part in first.get("loc", ()))
                msg = first.get("msg", "invalid")
                return f"spec.json validation failed: {loc}: {msg}"
            return "spec.json validation failed"
        if isinstance(exc, KeyError):
            return f"unknown scenario_type marker {marker!r}"
        text = str(exc) or exc.__class__.__name__
        return text.splitlines()[0][:200]
    except Exception:
        return exc.__class__.__name__


def load_custom_scenarios_detailed(knowledge_root: Path) -> ScenarioRegistryLoadResult:
    """Load all custom scenarios under ``knowledge_root``.

    Returns both successfully-loaded scenarios and a tuple of
    :class:`ScenarioLoadError` for any directory that could not be loaded.

    Malformed directories never prevent other scenarios from loading, and
    never emit a traceback at WARNING level. The full traceback is available
    at DEBUG level for forensics.
    """
    custom_dir = knowledge_root / CUSTOM_SCENARIOS_DIR
    if not custom_dir.is_dir():
        return ScenarioRegistryLoadResult(loaded={}, skipped=())

    loaded: dict[str, type[Any]] = {}
    skipped: list[ScenarioLoadError] = []
    for entry in sorted(custom_dir.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name

        marker = _read_persisted_marker(entry)
        try:
            cls = _load_family_class(custom_dir, name, marker)
            loaded[name] = cls
        except FileNotFoundError:
            # Spec-only directories without compiled source are Failure B
            # territory (AC-563). Preserve today's silent-skip behavior here.
            continue
        except Exception as exc:
            spec_path = entry / "spec.json"
            reason = _summarize_load_failure(exc, marker)
            skipped.append(
                ScenarioLoadError(
                    name=name,
                    spec_path=spec_path,
                    reason=reason,
                    marker=marker,
                )
            )
            logger.warning(
                "custom scenario %r skipped (%s): %s",
                name,
                spec_path,
                reason,
            )
            logger.debug(
                "custom scenario %r skipped (%s): full traceback",
                name,
                spec_path,
                exc_info=True,
            )

    return ScenarioRegistryLoadResult(
        loaded=loaded,
        skipped=tuple(skipped),
    )


def load_all_custom_scenarios(knowledge_root: Path) -> dict[str, type[Any]]:
    """Backwards-compatible entry point — returns only the successful loads."""
    return dict(load_custom_scenarios_detailed(knowledge_root).loaded)
