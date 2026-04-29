from __future__ import annotations

import importlib
import importlib.util
import inspect
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

from autocontext.extensions.hooks import ExtensionAPI, HookBus


def load_extensions(refs: str | Iterable[str], bus: HookBus) -> list[str]:
    """Load extension modules and let them register hooks on ``bus``.

    References may be ``module``, ``module:callable``, or a local ``.py`` file.
    A module without an explicit callable may expose ``register``, ``configure``,
    or ``setup``.
    """

    loaded: list[str] = []
    api = ExtensionAPI(bus)
    for ref in _split_refs(refs):
        target = _load_target(ref)
        _invoke_extension(target, api)
        loaded.append(ref)
    return loaded


def _split_refs(refs: str | Iterable[str]) -> list[str]:
    if isinstance(refs, str):
        return [part.strip() for part in refs.split(",") if part.strip()]
    return [str(part).strip() for part in refs if str(part).strip()]


def _load_target(ref: str) -> Any:
    module_ref, sep, attr = ref.partition(":")
    module = _load_module(module_ref)
    if sep:
        target: Any = module
        for part in attr.split("."):
            target = getattr(target, part)
        return target
    for name in ("register", "configure", "setup"):
        target = getattr(module, name, None)
        if callable(target):
            return target
    return module


def _load_module(module_ref: str) -> ModuleType:
    path = Path(module_ref).expanduser()
    if module_ref.endswith(".py") or path.exists():
        resolved = path.resolve()
        module_name = f"autocontext_user_extension_{abs(hash(str(resolved)))}"
        spec = importlib.util.spec_from_file_location(module_name, resolved)
        if spec is None or spec.loader is None:
            raise ImportError(f"could not load extension module from {resolved}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return importlib.import_module(module_ref)


def _invoke_extension(target: Any, api: ExtensionAPI) -> None:
    if inspect.isclass(target):
        target = target()
    if isinstance(target, ModuleType):
        register = getattr(target, "register", None)
        if not callable(register):
            raise ValueError(f"extension module {target.__name__!r} has no register/configure/setup callable")
        _call(register, api)
        return
    if hasattr(target, "register") and callable(target.register):
        target.register(api)
        return
    if callable(target):
        result = _call(target, api)
        if result is not None and hasattr(result, "register") and callable(result.register):
            result.register(api)
        return
    raise TypeError(f"unsupported extension target: {target!r}")


def _call(func: Any, api: ExtensionAPI) -> Any:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return func(api)
    required = [
        param
        for param in signature.parameters.values()
        if param.default is inspect.Signature.empty
        and param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY)
    ]
    if not required:
        return func()
    return func(api)
