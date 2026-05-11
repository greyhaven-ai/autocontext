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
