from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from autocontext.scenarios.base import ScenarioInterface

_GENERATED_PACKAGE_NAME = "autocontext.scenarios.custom.generated"


def _ensure_generated_package(custom_dir: Path) -> None:
    package = sys.modules.get(_GENERATED_PACKAGE_NAME)
    custom_dir_str = str(custom_dir)

    if package is None:
        import autocontext.scenarios.custom as custom_pkg

        package = ModuleType(_GENERATED_PACKAGE_NAME)
        package.__package__ = _GENERATED_PACKAGE_NAME
        package.__path__ = [custom_dir_str]  # type: ignore[attr-defined]
        sys.modules[_GENERATED_PACKAGE_NAME] = package
        custom_pkg.__dict__["generated"] = package
        return

    paths = list(getattr(package, "__path__", []))
    if custom_dir_str not in paths:
        package.__path__ = [*paths, custom_dir_str]  # type: ignore[attr-defined]


def load_custom_module_from_path(
    module_name: str,
    source_path: Path,
    *,
    force_reload: bool = False,
) -> ModuleType:
    custom_dir = source_path.parent.parent
    _ensure_generated_package(custom_dir)

    if force_reload and module_name in sys.modules:
        del sys.modules[module_name]
        importlib.invalidate_caches()

    if module_name in sys.modules:
        mod = sys.modules[module_name]
        if isinstance(mod, ModuleType):
            return mod
        raise ImportError(f"module slot for {module_name} is not a module")

    spec = importlib.util.spec_from_file_location(module_name, str(source_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create module spec for {source_path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_custom_scenario(
    custom_dir: Path,
    name: str,
    interface_class: type[Any] = ScenarioInterface,
    *,
    force_reload: bool = False,
) -> type[Any]:
    module_name = f"autocontext.scenarios.custom.generated.{name}"
    source_path = custom_dir / name / "scenario.py"
    if not source_path.exists():
        raise FileNotFoundError(f"custom scenario source not found: {source_path}")

    mod = load_custom_module_from_path(module_name, source_path, force_reload=force_reload)

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
