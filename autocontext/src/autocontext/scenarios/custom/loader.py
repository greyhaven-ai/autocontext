from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from autocontext.scenarios.base import ScenarioInterface


def load_custom_scenario(
    custom_dir: Path,
    name: str,
    interface_class: type[Any] = ScenarioInterface,
) -> type[Any]:
    module_name = f"autocontext.scenarios.custom.generated.{name}"

    if module_name in sys.modules:
        mod = sys.modules[module_name]
    else:
        source_path = custom_dir / name / "scenario.py"
        if not source_path.exists():
            raise FileNotFoundError(f"custom scenario source not found: {source_path}")

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
            and issubclass(attr, interface_class)
            and attr is not interface_class
            and getattr(attr, "name", None) == name
        ):
            return attr

    raise ImportError(
        f"no {interface_class.__name__} subclass with name='{name}' found in {module_name}"
    )
