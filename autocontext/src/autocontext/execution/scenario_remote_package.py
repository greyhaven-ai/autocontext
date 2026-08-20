"""Hermetic, dependency-free scenario packages for clean remote runtimes (AC-982)."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import io
import json
import sys
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from autocontext.runtime_images import PINNED_PYTHON_RUNTIME_IMAGE, require_pinned_runtime_image
from autocontext.scenarios.base import ScenarioInterface

REMOTE_PACKAGE_FORMAT = "autocontext-scenario-v1"
REMOTE_RUNTIME = "cpython-3.11-stdlib"
DEFAULT_REMOTE_RUNTIME_IMAGE = PINNED_PYTHON_RUNTIME_IMAGE


@dataclass(frozen=True, slots=True)
class RemoteScenarioPackage:
    content: bytes
    sha256: str
    manifest: dict[str, Any]


def build_remote_scenario_package(
    scenario: ScenarioInterface,
    strategy: dict[str, Any],
    seed: int,
    *,
    initial_state: Mapping[str, Any] | None = None,
    initial_observation: Mapping[str, Any] | None = None,
    fixture_digest: str | None = None,
) -> RemoteScenarioPackage:
    """Build a deterministic zipapp containing a scenario's local source closure."""

    prepared_fields = (
        initial_state is not None,
        initial_observation is not None,
        fixture_digest is not None,
    )
    if any(prepared_fields) and not all(prepared_fields):
        raise ValueError("prepared fixture state, observation, and digest must be supplied together")
    if fixture_digest is not None and not _is_sha256(fixture_digest):
        raise ValueError("prepared fixture digest must be a lowercase sha256 hex digest")

    scenario_module = inspect.getmodule(type(scenario))
    if scenario_module is None:
        raise ValueError(f"cannot locate scenario module for {type(scenario).__name__}")
    module_name = type(scenario).__module__
    scenario_sources, dependencies = _collect_scenario_sources(scenario_module)
    sources: dict[str, str] = {
        "__main__.py": _ENTRYPOINT,
        "autocontext/scenarios/base.py": _STDLIB_SCENARIO_ABI,
        **scenario_sources,
    }
    package_paths = {
        *_package_init_paths(module_name),
        *_package_init_paths("autocontext.scenarios.base"),
    }
    for package_path in package_paths:
        sources.setdefault(package_path, "")
    scenario_state = _scenario_instance_state(scenario)
    encoded_strategy = _json_mapping(strategy)
    encoded_initial_state = _json_mapping(initial_state) if initial_state is not None else None
    encoded_initial_observation = _json_mapping(initial_observation) if initial_observation is not None else None
    if (
        encoded_initial_state is not None
        and encoded_initial_observation is not None
        and fixture_digest != _runtime_fixture_digest(encoded_initial_state, encoded_initial_observation)
    ):
        raise ValueError("prepared fixture digest does not match its canonical state and observation")
    payload = {
        "scenario_name": scenario.name,
        "scenario_module": module_name,
        "scenario_class": type(scenario).__name__,
        "scenario_state": scenario_state,
        "strategy": encoded_strategy,
        "seed": int(seed),
        "initial_state": encoded_initial_state,
        "initial_observation": encoded_initial_observation,
        "fixture_digest": fixture_digest,
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    state_bytes = json.dumps(scenario_state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    strategy_bytes = json.dumps(encoded_strategy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    files: dict[str, bytes] = {name: source.encode("utf-8") for name, source in sources.items()}
    files["autocontext-payload.json"] = payload_bytes
    manifest: dict[str, Any] = {
        "format": REMOTE_PACKAGE_FORMAT,
        "runtime": REMOTE_RUNTIME,
        "scenario_module": module_name,
        "scenario_class": type(scenario).__name__,
        "dependencies": sorted(dependencies),
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "scenario_state_sha256": hashlib.sha256(state_bytes).hexdigest(),
        "strategy_sha256": hashlib.sha256(strategy_bytes).hexdigest(),
        "seed": int(seed),
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())},
    }
    if encoded_initial_state is not None and encoded_initial_observation is not None:
        fixture_state_bytes = _canonical_json_bytes(encoded_initial_state)
        fixture_observation_bytes = _canonical_json_bytes(encoded_initial_observation)
        manifest.update(
            {
                "fixture_digest": fixture_digest,
                "fixture_state_sha256": hashlib.sha256(fixture_state_bytes).hexdigest(),
                "fixture_observation_sha256": hashlib.sha256(fixture_observation_bytes).hexdigest(),
            }
        )
    files["autocontext-manifest.json"] = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    content = _deterministic_zip(files)
    package = RemoteScenarioPackage(content=content, sha256=hashlib.sha256(content).hexdigest(), manifest=manifest)
    preflight_remote_scenario_package(package)
    return package


def preflight_remote_scenario_package(package: RemoteScenarioPackage) -> None:
    """Verify archive shape, source syntax, file digests, and the declared runtime."""

    if hashlib.sha256(package.content).hexdigest() != package.sha256:
        raise ValueError("remote scenario package digest mismatch")
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        names = set(archive.namelist())
        required = {"__main__.py", "autocontext-manifest.json", "autocontext-payload.json"}
        missing = required - names
        if missing:
            raise ValueError(f"remote scenario package missing files: {', '.join(sorted(missing))}")
        manifest = json.loads(archive.read("autocontext-manifest.json"))
        if manifest != package.manifest or manifest.get("format") != REMOTE_PACKAGE_FORMAT:
            raise ValueError("remote scenario package manifest mismatch")
        if manifest.get("runtime") != REMOTE_RUNTIME:
            raise ValueError("remote scenario package runtime mismatch")
        payload = json.loads(archive.read("autocontext-payload.json"))
        if not isinstance(payload, dict):
            raise ValueError("remote scenario package payload must be an object")
        canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        canonical_state = json.dumps(payload.get("scenario_state"), sort_keys=True, separators=(",", ":")).encode("utf-8")
        canonical_strategy = json.dumps(payload.get("strategy"), sort_keys=True, separators=(",", ":")).encode("utf-8")
        provenance_digests = {
            "payload_sha256": hashlib.sha256(canonical_payload).hexdigest(),
            "scenario_state_sha256": hashlib.sha256(canonical_state).hexdigest(),
            "strategy_sha256": hashlib.sha256(canonical_strategy).hexdigest(),
        }
        for field_name, digest in provenance_digests.items():
            if manifest.get(field_name) != digest:
                raise ValueError(f"remote scenario package {field_name} mismatch")
        if manifest.get("seed") != payload.get("seed"):
            raise ValueError("remote scenario package seed provenance mismatch")
        _validate_prepared_fixture_provenance(payload, manifest)
        declared_files = manifest.get("files")
        if not isinstance(declared_files, dict):
            raise ValueError("remote scenario package manifest lacks file digests")
        dependencies = manifest.get("dependencies")
        if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
            raise ValueError("remote scenario package dependency manifest is invalid")
        for dependency in dependencies:
            module_path = dependency.replace(".", "/")
            if f"{module_path}.py" not in names and f"{module_path}/__init__.py" not in names:
                raise ValueError(f"remote scenario package dependency is missing: {dependency}")
        for name, digest in declared_files.items():
            if name not in names or hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError(f"remote scenario package file digest mismatch: {name}")
        for name in names:
            if name.endswith(".py"):
                compile(archive.read(name), name, "exec")


def _module_source(module: ModuleType) -> str:
    source_path = inspect.getsourcefile(module)
    if source_path is None:
        raise ValueError(f"scenario module has no source file: {module.__name__}")
    with open(source_path, encoding="utf-8") as source_file:  # noqa: PTH123 - inspect returns an exact module path
        return source_file.read()


def _collect_scenario_sources(module: ModuleType) -> tuple[dict[str, str], frozenset[str]]:
    module_name = module.__name__
    source_path = inspect.getsourcefile(module)
    if source_path is None:
        raise ValueError(f"scenario module has no source file: {module_name}")
    resolved_source = Path(source_path).resolve()
    parts = module_name.split(".")
    root_index = len(parts) if resolved_source.name == "__init__.py" else len(parts) - 1
    try:
        source_root = resolved_source.parents[root_index]
    except IndexError as exc:
        raise ValueError(f"cannot determine source root for scenario module: {module_name}") from exc

    sources: dict[str, str] = {}
    dependencies: set[str] = set()
    pending: list[tuple[str, str, bool]] = [(module_name, _module_source(module), resolved_source.name == "__init__.py")]
    visited: set[str] = set()
    while pending:
        current_name, source, is_package = pending.pop()
        if current_name in visited:
            continue
        visited.add(current_name)
        sources[_module_archive_path(current_name, is_package)] = source
        for dependency, _relative, optional_submodule in _source_imports(current_name, source, is_package):
            root_name = dependency.split(".", 1)[0]
            if root_name in sys.stdlib_module_names or root_name == "__future__":
                continue
            if dependency == "autocontext.scenarios.base" or dependency.startswith("autocontext.scenarios.base."):
                continue
            resolved = _local_module_source(source_root, dependency)
            if resolved is None:
                if optional_submodule:
                    continue
                raise ValueError(f"remote scenario package has unbundled dependency: {dependency}")
            dependency_source, dependency_is_package = resolved
            dependencies.add(dependency)
            pending.append((dependency, dependency_source, dependency_is_package))
    return sources, frozenset(dependencies)


def _source_imports(module_name: str, source: str, is_package: bool) -> frozenset[tuple[str, bool, bool]]:
    imported: set[tuple[str, bool, bool]] = set()
    package_name = module_name if is_package else module_name.rpartition(".")[0]
    for node in ast.walk(ast.parse(source, filename=module_name)):
        if isinstance(node, ast.Import):
            imported.update((alias.name, False, False) for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            if not package_name:
                raise ValueError(f"remote scenario module uses an invalid relative import: {module_name}")
            relative_name = "." * node.level + (node.module or "")
            try:
                base_name = importlib.util.resolve_name(relative_name, package_name)
            except (ImportError, ValueError) as exc:
                raise ValueError(f"remote scenario module has invalid relative import: {relative_name}") from exc
        else:
            base_name = node.module or ""
        if base_name:
            imported.add((base_name, bool(node.level), False))
        for alias in node.names:
            if alias.name != "*" and base_name:
                imported.add((f"{base_name}.{alias.name}", bool(node.level), True))
    return frozenset(imported)


def _local_module_source(source_root: Path, module_name: str) -> tuple[str, bool] | None:
    relative = Path(*module_name.split("."))
    module_path = (source_root / relative).with_suffix(".py")
    package_path = source_root / relative / "__init__.py"
    candidate = package_path if package_path.is_file() else module_path if module_path.is_file() else None
    if candidate is None:
        # ``from package.module import Symbol`` also appears as a possible
        # submodule import. The base module has already been queued.
        return None
    resolved = candidate.resolve()
    try:
        resolved.relative_to(source_root)
    except ValueError as exc:
        raise ValueError(f"remote scenario dependency escapes its source root: {module_name}") from exc
    return resolved.read_text(encoding="utf-8"), resolved.name == "__init__.py"


def _module_archive_path(module_name: str, is_package: bool) -> str:
    relative = module_name.replace(".", "/")
    return f"{relative}/__init__.py" if is_package else f"{relative}.py"


def _package_init_paths(module_name: str) -> tuple[str, ...]:
    parts = module_name.split(".")[:-1]
    return tuple("/".join(parts[:index]) + "/__init__.py" for index in range(1, len(parts) + 1))


def _scenario_instance_state(scenario: ScenarioInterface) -> dict[str, Any]:
    state: dict[str, Any] = {}
    instance_dict = getattr(scenario, "__dict__", None)
    if isinstance(instance_dict, dict):
        state.update(instance_dict)
    for scenario_type in reversed(type(scenario).__mro__):
        declared_slots = scenario_type.__dict__.get("__slots__", ())
        slots = (declared_slots,) if isinstance(declared_slots, str) else declared_slots
        for name in slots:
            if name in {"__dict__", "__weakref__"} or name in state:
                continue
            try:
                state[name] = getattr(scenario, name)
            except AttributeError:
                continue
    return _json_mapping(state)


def _json_mapping(value: Any) -> dict[str, Any]:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError("remote scenario state and strategy must be JSON objects")
    return {str(key): item for key, item in decoded.items()}


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _validate_prepared_fixture_provenance(
    payload: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> None:
    state = payload.get("initial_state")
    observation = payload.get("initial_observation")
    fixture_digest = payload.get("fixture_digest")
    prepared_fields = (state is not None, observation is not None, fixture_digest is not None)
    manifest_fields = tuple(
        manifest.get(field) is not None
        for field in (
            "fixture_digest",
            "fixture_state_sha256",
            "fixture_observation_sha256",
        )
    )
    if any(prepared_fields) and not all(prepared_fields):
        raise ValueError("remote scenario package has incomplete prepared fixture payload")
    if any(manifest_fields) and not all(manifest_fields):
        raise ValueError("remote scenario package has incomplete prepared fixture manifest")
    if not any(prepared_fields):
        if any(manifest_fields):
            raise ValueError("ordinary remote scenario package declares prepared fixture provenance")
        return
    if not all(manifest_fields) or not isinstance(state, dict) or not isinstance(observation, dict):
        raise ValueError("prepared remote scenario package lacks bound fixture provenance")
    if not _is_sha256(fixture_digest) or manifest.get("fixture_digest") != fixture_digest:
        raise ValueError("remote scenario package fixture digest mismatch")
    if fixture_digest != _runtime_fixture_digest(state, observation):
        raise ValueError("remote scenario package fixture digest does not match its state and observation")
    expected = {
        "fixture_state_sha256": hashlib.sha256(_canonical_json_bytes(state)).hexdigest(),
        "fixture_observation_sha256": hashlib.sha256(_canonical_json_bytes(observation)).hexdigest(),
    }
    for field_name, digest in expected.items():
        if manifest.get(field_name) != digest:
            raise ValueError(f"remote scenario package {field_name} mismatch")


def _runtime_fixture_digest(
    state: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> str:
    # Import lazily so the generic remote package stays below the context
    # package at process startup while sharing evaluation's canonical JSON.
    from autocontext.context_bundles.runtime_evaluator import runtime_fixture_digest

    return runtime_fixture_digest(state, observation)


def _deterministic_zip(files: dict[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)
    return stream.getvalue()


_ENTRYPOINT = """from __future__ import annotations
import hashlib
import importlib
import json
import sys
import zipfile

try:
    with zipfile.ZipFile(sys.argv[0]) as archive:
        manifest = json.loads(archive.read("autocontext-manifest.json"))
        if manifest.get("format") != "autocontext-scenario-v1":
            raise ValueError("scenario package format mismatch")
        if manifest.get("runtime") != "cpython-3.11-stdlib":
            raise ValueError("scenario package runtime mismatch")
        for name, digest in manifest.get("files", {}).items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError(f"scenario package file digest mismatch: {name}")
        payload = json.loads(archive.read("autocontext-payload.json"))
    module = importlib.import_module(payload["scenario_module"])
    scenario_type = getattr(module, payload["scenario_class"])
    scenario = object.__new__(scenario_type)
    for name, value in payload["scenario_state"].items():
        object.__setattr__(scenario, name, value)
except Exception as exc:
    print(json.dumps({"autocontext_bootstrap_error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
    raise SystemExit(70)
if payload.get("initial_state") is None:
    result = scenario.execute_match(payload["strategy"], int(payload["seed"]))
else:
    result = scenario.execute_match_from_state(
        payload["strategy"],
        int(payload["seed"]),
        payload["initial_state"],
    )
replay = {
    "scenario": scenario.name,
    "seed": int(payload["seed"]),
    "narrative": scenario.replay_to_narrative(result.replay),
    "timeline": result.replay,
}
output = {"result": result.model_dump(mode="json"), "replay": replay}
if payload.get("fixture_digest") is not None:
    output["fixture_digest"] = payload["fixture_digest"]
print(json.dumps(output, sort_keys=True))
"""


_STDLIB_SCENARIO_ABI = """from __future__ import annotations
from dataclasses import asdict, dataclass, field

@dataclass
class Observation:
    narrative: str
    state: dict = field(default_factory=dict)
    constraints: list = field(default_factory=list)

@dataclass
class Result:
    score: float
    summary: str
    winner: str | None = None
    replay: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    validation_errors: list = field(default_factory=list)

    @property
    def passed_validation(self):
        return not self.validation_errors

    def model_dump(self, mode="python"):
        del mode
        return asdict(self)

class ScenarioInterface:
    def seed_tools(self):
        return {}

    def custom_backpressure(self, result):
        return {"score": result.score}

    def execute_match(self, strategy, seed):
        state = self.initial_state(seed=seed)
        return self.execute_match_from_state(strategy, seed, state)

    def execute_match_from_state(self, strategy, seed, initial_state):
        del seed
        if type(self).execute_match is not ScenarioInterface.execute_match:
            raise NotImplementedError(
                "scenario overrides execute_match and must implement execute_match_from_state"
            )
        state = dict(initial_state)
        valid, reason = self.validate_actions(state, "challenger", strategy)
        if not valid:
            return Result(
                score=0.0,
                winner="incumbent",
                summary="strategy rejected during validation",
                replay=[{"event": "validation_failed", "reason": reason}],
                metrics={"valid": 0.0},
                validation_errors=[reason],
            )
        next_state = self.step(state, strategy)
        if not self.is_terminal(next_state):
            next_state = {**dict(next_state), "terminal": True}
        return self.get_result(next_state)
"""


__all__ = [
    "DEFAULT_REMOTE_RUNTIME_IMAGE",
    "REMOTE_PACKAGE_FORMAT",
    "REMOTE_RUNTIME",
    "RemoteScenarioPackage",
    "build_remote_scenario_package",
    "preflight_remote_scenario_package",
    "require_pinned_runtime_image",
]
