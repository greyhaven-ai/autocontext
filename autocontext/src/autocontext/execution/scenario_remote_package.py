"""Hermetic, dependency-free scenario packages for clean remote runtimes (AC-982)."""

from __future__ import annotations

import ast
import hashlib
import inspect
import io
import json
import re
import sys
import zipfile
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from autocontext.runtime_images import PINNED_PYTHON_RUNTIME_IMAGE
from autocontext.scenarios.base import ScenarioInterface

REMOTE_PACKAGE_FORMAT = "autocontext-scenario-v1"
REMOTE_RUNTIME = "cpython-3.11-stdlib"
DEFAULT_REMOTE_RUNTIME_IMAGE = PINNED_PYTHON_RUNTIME_IMAGE
_PINNED_IMAGE_PATTERN = re.compile(r"@sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class RemoteScenarioPackage:
    content: bytes
    sha256: str
    manifest: dict[str, Any]


def build_remote_scenario_package(
    scenario: ScenarioInterface,
    strategy: dict[str, Any],
    seed: int,
) -> RemoteScenarioPackage:
    """Build a deterministic zipapp containing scenario source and a stdlib ABI shim."""

    scenario_module = inspect.getmodule(type(scenario))
    if scenario_module is None:
        raise ValueError(f"cannot locate scenario module for {type(scenario).__name__}")
    module_name = type(scenario).__module__
    module_source = _module_source(scenario_module)
    module_path = module_name.replace(".", "/") + ".py"
    sources = {
        "__main__.py": _ENTRYPOINT,
        "autocontext/scenarios/base.py": _STDLIB_SCENARIO_ABI,
        module_path: module_source,
    }
    package_paths = {
        *_package_init_paths(module_name),
        *_package_init_paths("autocontext.scenarios.base"),
    }
    for package_path in package_paths:
        sources.setdefault(package_path, "")
    _validate_imports(module_name, module_source, frozenset(sources))
    scenario_state = _json_mapping(vars(scenario)) if hasattr(scenario, "__dict__") else {}
    payload = {
        "scenario_name": scenario.name,
        "scenario_module": module_name,
        "scenario_class": type(scenario).__name__,
        "scenario_state": scenario_state,
        "strategy": _json_mapping(strategy),
        "seed": int(seed),
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    files: dict[str, bytes] = {name: source.encode("utf-8") for name, source in sources.items()}
    files["autocontext-payload.json"] = payload_bytes
    manifest: dict[str, Any] = {
        "format": REMOTE_PACKAGE_FORMAT,
        "runtime": REMOTE_RUNTIME,
        "scenario_module": module_name,
        "scenario_class": type(scenario).__name__,
        "dependencies": [],
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())},
    }
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
        declared_files = manifest.get("files")
        if not isinstance(declared_files, dict):
            raise ValueError("remote scenario package manifest lacks file digests")
        for name, digest in declared_files.items():
            if name not in names or hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError(f"remote scenario package file digest mismatch: {name}")
        for name in names:
            if name.endswith(".py"):
                compile(archive.read(name), name, "exec")


def require_pinned_runtime_image(image: str) -> None:
    if not _PINNED_IMAGE_PATTERN.search(image):
        raise ValueError("remote scenario image must use an immutable @sha256 digest")


def _module_source(module: ModuleType) -> str:
    source_path = inspect.getsourcefile(module)
    if source_path is None:
        raise ValueError(f"scenario module has no source file: {module.__name__}")
    with open(source_path, encoding="utf-8") as source_file:  # noqa: PTH123 - inspect returns an exact module path
        return source_file.read()


def _package_init_paths(module_name: str) -> tuple[str, ...]:
    parts = module_name.split(".")[:-1]
    return tuple("/".join(parts[:index]) + "/__init__.py" for index in range(1, len(parts) + 1))


def _validate_imports(module_name: str, source: str, packaged_paths: frozenset[str]) -> None:
    packaged_modules = {path.removesuffix("/__init__.py").replace("/", ".") for path in packaged_paths}
    packaged_modules.update(path.removesuffix(".py").replace("/", ".") for path in packaged_paths if path.endswith(".py"))
    allowed_roots = {*sys.stdlib_module_names, "__future__", "autocontext"}
    for node in ast.walk(ast.parse(source, filename=module_name)):
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported = [node.module or ""]
        else:
            continue
        for name in imported:
            root = name.split(".", 1)[0]
            if root not in allowed_roots and name not in packaged_modules:
                raise ValueError(f"remote scenario package has unbundled dependency: {name}")
            if root == "autocontext" and name not in packaged_modules and name != "autocontext.scenarios.base":
                raise ValueError(f"remote scenario package has unbundled autocontext module: {name}")


def _json_mapping(value: Any) -> dict[str, Any]:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"))
    decoded = json.loads(encoded)
    if not isinstance(decoded, dict):
        raise ValueError("remote scenario state and strategy must be JSON objects")
    return {str(key): item for key, item in decoded.items()}


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
    scenario = getattr(module, payload["scenario_class"])()
    for name, value in payload["scenario_state"].items():
        setattr(scenario, name, value)
except Exception as exc:
    print(json.dumps({"autocontext_bootstrap_error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
    raise SystemExit(70)
result = scenario.execute_match(payload["strategy"], int(payload["seed"]))
replay = {
    "scenario": scenario.name,
    "seed": int(payload["seed"]),
    "narrative": scenario.replay_to_narrative(result.replay),
    "timeline": result.replay,
}
print(json.dumps({"result": result.model_dump(mode="json"), "replay": replay}, sort_keys=True))
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
