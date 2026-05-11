"""Runtime context layering and cwd-scoped discovery contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from autocontext.session.skill_registry import SkillRegistry

REPO_INSTRUCTION_FILENAMES = ("AGENTS.md", "CLAUDE.md")
RUNTIME_SKILL_DIRS = (".autoctx/skills", ".claude/skills", ".codex/skills", "skills")


class RuntimeContextLayerKey(StrEnum):
    SYSTEM_POLICY = "system_policy"
    REPO_INSTRUCTIONS = "repo_instructions"
    ROLE_INSTRUCTIONS = "role_instructions"
    SCENARIO_CONTEXT = "scenario_context"
    KNOWLEDGE = "knowledge"
    RUNTIME_SKILLS = "runtime_skills"
    TOOL_AFFORDANCES = "tool_affordances"
    SESSION_HISTORY = "session_history"


@dataclass(frozen=True, slots=True)
class RuntimeContextLayer:
    key: RuntimeContextLayerKey
    order: int
    owner: str
    persistence: str
    budget: str
    child_task_behavior: str


@dataclass(frozen=True, slots=True)
class RepoInstruction:
    path: Path
    relative_path: str
    content: str


@dataclass(frozen=True, slots=True)
class RuntimeContextDiscoveryRequest:
    workspace_root: Path
    cwd: str | Path = "/"
    configured_skill_roots: Sequence[Path] = field(default_factory=tuple)

    def for_child_task(self, cwd: str | Path) -> RuntimeContextDiscoveryRequest:
        return RuntimeContextDiscoveryRequest(
            workspace_root=self.workspace_root,
            cwd=cwd,
            configured_skill_roots=tuple(self.configured_skill_roots),
        )


@dataclass(frozen=True, slots=True)
class RuntimeContextBundleEntry:
    entry_id: str
    title: str
    content: str
    provenance: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RuntimeContextLayerBundle:
    layer: RuntimeContextLayer
    entries: tuple[RuntimeContextBundleEntry, ...] = ()


@dataclass(frozen=True, slots=True)
class RuntimeContextBundle:
    layers: tuple[RuntimeContextLayerBundle, ...]

    def get_layer(self, key: RuntimeContextLayerKey) -> RuntimeContextLayerBundle:
        for layer in self.layers:
            if layer.layer.key == key:
                return layer
        raise KeyError(f"unknown runtime context layer: {key}")

    def all_entries(self) -> tuple[RuntimeContextBundleEntry, ...]:
        return tuple(entry for layer in self.layers for entry in layer.entries)


@dataclass(frozen=True, slots=True)
class RuntimeContextAssemblyRequest:
    discovery: RuntimeContextDiscoveryRequest
    system_policy: str = ""
    role_instructions: str = ""
    scenario_context: str = ""
    knowledge_components: Mapping[str, str] = field(default_factory=dict)
    knowledge_include: Sequence[str] | None = None
    knowledge_exclude: Sequence[str] = ()
    tool_affordances: Mapping[str, str] = field(default_factory=dict)
    session_history: Sequence[str] = ()

    def for_child_task(
        self,
        cwd: str | Path,
        *,
        scenario_context: str = "",
        session_history: Sequence[str] = (),
    ) -> RuntimeContextAssemblyRequest:
        return RuntimeContextAssemblyRequest(
            discovery=self.discovery.for_child_task(cwd),
            system_policy=self.system_policy,
            role_instructions=self.role_instructions,
            scenario_context=scenario_context,
            knowledge_components=dict(self.knowledge_components),
            knowledge_include=tuple(self.knowledge_include) if self.knowledge_include is not None else None,
            knowledge_exclude=tuple(self.knowledge_exclude),
            tool_affordances=dict(self.tool_affordances),
            session_history=tuple(session_history),
        )


RUNTIME_CONTEXT_LAYERS = (
    RuntimeContextLayer(
        key=RuntimeContextLayerKey.SYSTEM_POLICY,
        order=1,
        owner="runtime",
        persistence="bundled",
        budget="protected",
        child_task_behavior="inherit",
    ),
    RuntimeContextLayer(
        key=RuntimeContextLayerKey.REPO_INSTRUCTIONS,
        order=2,
        owner="workspace",
        persistence="repo",
        budget="protected",
        child_task_behavior="recompute_from_child_cwd",
    ),
    RuntimeContextLayer(
        key=RuntimeContextLayerKey.ROLE_INSTRUCTIONS,
        order=3,
        owner="autocontext",
        persistence="bundled",
        budget="protected",
        child_task_behavior="inherit_or_override_by_role",
    ),
    RuntimeContextLayer(
        key=RuntimeContextLayerKey.SCENARIO_CONTEXT,
        order=4,
        owner="scenario",
        persistence="run",
        budget="protected",
        child_task_behavior="inherit_task_slice",
    ),
    RuntimeContextLayer(
        key=RuntimeContextLayerKey.KNOWLEDGE,
        order=5,
        owner="knowledge",
        persistence="knowledge",
        budget="compress",
        child_task_behavior="include_applicable_knowledge",
    ),
    RuntimeContextLayer(
        key=RuntimeContextLayerKey.RUNTIME_SKILLS,
        order=6,
        owner="workspace",
        persistence="repo_or_skill_store",
        budget="manifest_first",
        child_task_behavior="recompute_from_child_cwd",
    ),
    RuntimeContextLayer(
        key=RuntimeContextLayerKey.TOOL_AFFORDANCES,
        order=7,
        owner="runtime",
        persistence="ephemeral",
        budget="summarize",
        child_task_behavior="inherit_scoped_grants",
    ),
    RuntimeContextLayer(
        key=RuntimeContextLayerKey.SESSION_HISTORY,
        order=8,
        owner="runtime_session",
        persistence="runtime_session_log",
        budget="compact",
        child_task_behavior="recompute_from_child_session",
    ),
)
RUNTIME_CONTEXT_LAYER_KEYS = tuple(layer.key for layer in RUNTIME_CONTEXT_LAYERS)


def discover_repo_instructions(request: RuntimeContextDiscoveryRequest) -> tuple[RepoInstruction, ...]:
    root = _workspace_root(request)
    cwd = _resolve_cwd(root, request.cwd)
    instructions: list[RepoInstruction] = []
    for directory in _ancestor_dirs(root, cwd, nearest_first=False):
        for filename in REPO_INSTRUCTION_FILENAMES:
            path = directory / filename
            if not path.is_file():
                continue
            instructions.append(
                RepoInstruction(
                    path=path,
                    relative_path=_relative_posix(path, root),
                    content=path.read_text(encoding="utf-8"),
                )
            )
    return tuple(instructions)


def runtime_skill_discovery_roots(request: RuntimeContextDiscoveryRequest) -> tuple[Path, ...]:
    root = _workspace_root(request)
    cwd = _resolve_cwd(root, request.cwd)
    roots: list[Path] = []
    seen: set[Path] = set()

    for configured_root in request.configured_skill_roots:
        _append_existing_unique_dir(roots, seen, _resolve_configured_root(root, configured_root))

    for directory in _ancestor_dirs(root, cwd, nearest_first=True):
        for skill_dir in RUNTIME_SKILL_DIRS:
            _append_existing_unique_dir(roots, seen, directory / skill_dir)
    return tuple(roots)


def discover_runtime_skills(request: RuntimeContextDiscoveryRequest) -> SkillRegistry:
    registry = SkillRegistry()
    for root in runtime_skill_discovery_roots(request):
        registry.discover(root)
    return registry


def select_runtime_knowledge_components(
    components: Mapping[str, str],
    *,
    include: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
) -> dict[str, str]:
    allowed = set(include) if include is not None else None
    blocked = set(exclude)
    selected: dict[str, str] = {}
    for key, value in components.items():
        if allowed is not None and key not in allowed:
            continue
        if key in blocked or not value:
            continue
        selected[key] = value
    return selected


def assemble_runtime_context(request: RuntimeContextAssemblyRequest) -> RuntimeContextBundle:
    entries_by_layer: dict[RuntimeContextLayerKey, tuple[RuntimeContextBundleEntry, ...]] = {
        RuntimeContextLayerKey.SYSTEM_POLICY: _single_text_entry(
            "system_policy:default",
            "System Policy",
            request.system_policy,
            source_type="system_policy",
        ),
        RuntimeContextLayerKey.REPO_INSTRUCTIONS: _repo_instruction_entries(request.discovery),
        RuntimeContextLayerKey.ROLE_INSTRUCTIONS: _single_text_entry(
            "role_instructions:default",
            "Role Instructions",
            request.role_instructions,
            source_type="role_instructions",
        ),
        RuntimeContextLayerKey.SCENARIO_CONTEXT: _single_text_entry(
            "scenario_context:default",
            "Scenario Context",
            request.scenario_context,
            source_type="scenario_context",
        ),
        RuntimeContextLayerKey.KNOWLEDGE: _knowledge_entries(request),
        RuntimeContextLayerKey.RUNTIME_SKILLS: _runtime_skill_entries(request.discovery),
        RuntimeContextLayerKey.TOOL_AFFORDANCES: _mapping_entries(
            request.tool_affordances,
            entry_id_prefix="tool_affordance",
            source_type="tool_affordance",
        ),
        RuntimeContextLayerKey.SESSION_HISTORY: _session_history_entries(request.session_history),
    }
    return RuntimeContextBundle(
        layers=tuple(
            RuntimeContextLayerBundle(layer=layer, entries=entries_by_layer.get(layer.key, ()))
            for layer in RUNTIME_CONTEXT_LAYERS
        )
    )


def _workspace_root(request: RuntimeContextDiscoveryRequest) -> Path:
    return request.workspace_root.resolve()


def _resolve_cwd(root: Path, cwd: str | Path) -> Path:
    raw_cwd = str(cwd)
    candidate = root / raw_cwd.lstrip("/") if raw_cwd.startswith("/") else root / raw_cwd
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Runtime context cwd escapes workspace root: {cwd}") from exc
    return resolved


def _resolve_configured_root(root: Path, skill_root: Path) -> Path:
    if skill_root.is_absolute():
        return skill_root.resolve()
    return (root / skill_root).resolve()


def _ancestor_dirs(root: Path, cwd: Path, *, nearest_first: bool) -> tuple[Path, ...]:
    dirs: list[Path] = []
    current = cwd
    while True:
        dirs.append(current)
        if current == root:
            break
        current = current.parent
    return tuple(dirs if nearest_first else reversed(dirs))


def _append_existing_unique_dir(roots: list[Path], seen: set[Path], path: Path) -> None:
    if path in seen or not path.is_dir():
        return
    seen.add(path)
    roots.append(path)


def _relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _single_text_entry(
    entry_id: str,
    title: str,
    content: str,
    *,
    source_type: str,
) -> tuple[RuntimeContextBundleEntry, ...]:
    if not content.strip():
        return ()
    return (
        RuntimeContextBundleEntry(
            entry_id=entry_id,
            title=title,
            content=content,
            provenance={"source_type": source_type},
        ),
    )


def _repo_instruction_entries(request: RuntimeContextDiscoveryRequest) -> tuple[RuntimeContextBundleEntry, ...]:
    return tuple(
        RuntimeContextBundleEntry(
            entry_id=f"repo_instruction:{instruction.relative_path}",
            title=instruction.relative_path,
            content=instruction.content,
            provenance={
                "source_type": "repo_instruction",
                "relative_path": instruction.relative_path,
                "path": str(instruction.path),
            },
        )
        for instruction in discover_repo_instructions(request)
    )


def _knowledge_entries(request: RuntimeContextAssemblyRequest) -> tuple[RuntimeContextBundleEntry, ...]:
    selected = select_runtime_knowledge_components(
        request.knowledge_components,
        include=request.knowledge_include,
        exclude=request.knowledge_exclude,
    )
    return _mapping_entries(
        selected,
        entry_id_prefix="knowledge",
        source_type="knowledge_component",
        provenance_key="component",
    )


def _runtime_skill_entries(request: RuntimeContextDiscoveryRequest) -> tuple[RuntimeContextBundleEntry, ...]:
    root = _workspace_root(request)
    entries: list[RuntimeContextBundleEntry] = []
    for manifest in discover_runtime_skills(request).all_manifests():
        provenance = {
            "source_type": "runtime_skill",
            "name": manifest.name,
            "path": str(manifest.skill_path),
        }
        relative_path = _relative_to_root(manifest.skill_path, root)
        if relative_path is not None:
            provenance["relative_path"] = relative_path
        entries.append(
            RuntimeContextBundleEntry(
                entry_id=f"runtime_skill:{manifest.name}",
                title=manifest.name,
                content=manifest.description,
                provenance=provenance,
                metadata={"manifest_first": "true"},
            )
        )
    return tuple(entries)


def _mapping_entries(
    values: Mapping[str, str],
    *,
    entry_id_prefix: str,
    source_type: str,
    provenance_key: str = "name",
) -> tuple[RuntimeContextBundleEntry, ...]:
    entries: list[RuntimeContextBundleEntry] = []
    for key, value in values.items():
        if not value.strip():
            continue
        entries.append(
            RuntimeContextBundleEntry(
                entry_id=f"{entry_id_prefix}:{key}",
                title=key,
                content=value,
                provenance={"source_type": source_type, provenance_key: key},
            )
        )
    return tuple(entries)


def _session_history_entries(history: Sequence[str]) -> tuple[RuntimeContextBundleEntry, ...]:
    entries: list[RuntimeContextBundleEntry] = []
    non_empty_history = [(index, content) for index, content in enumerate(history, start=1) if content.strip()]
    for visible_index, (source_index, content) in enumerate(non_empty_history, start=1):
        title = (
            "Recent Session History"
            if len(non_empty_history) == 1
            else f"Recent Session History #{visible_index}"
        )
        entries.append(
            RuntimeContextBundleEntry(
                entry_id=f"session_history:{source_index}",
                title=title,
                content=content,
                provenance={"source_type": "session_history", "index": str(source_index)},
            )
        )
    return tuple(entries)


def _relative_to_root(path: Path, root: Path) -> str | None:
    try:
        return _relative_posix(path.resolve(), root)
    except ValueError:
        return None
