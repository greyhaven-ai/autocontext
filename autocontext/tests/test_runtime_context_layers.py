from __future__ import annotations

from pathlib import Path


def _write_skill(root: Path, name: str, description: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"""---
name: {name}
description: {description}
---

# {name}

Instructions for {name}.
""",
        encoding="utf-8",
    )
    return skill_dir


def test_runtime_context_layer_order_is_canonical() -> None:
    from autocontext.session.runtime_context import (
        RUNTIME_CONTEXT_LAYER_KEYS,
        RUNTIME_CONTEXT_LAYERS,
        RuntimeContextLayerKey,
    )

    assert RUNTIME_CONTEXT_LAYER_KEYS == (
        RuntimeContextLayerKey.SYSTEM_POLICY,
        RuntimeContextLayerKey.REPO_INSTRUCTIONS,
        RuntimeContextLayerKey.ROLE_INSTRUCTIONS,
        RuntimeContextLayerKey.SCENARIO_CONTEXT,
        RuntimeContextLayerKey.KNOWLEDGE,
        RuntimeContextLayerKey.RUNTIME_SKILLS,
        RuntimeContextLayerKey.TOOL_AFFORDANCES,
        RuntimeContextLayerKey.SESSION_HISTORY,
    )
    assert [layer.order for layer in RUNTIME_CONTEXT_LAYERS] == list(range(1, 9))
    assert {layer.key for layer in RUNTIME_CONTEXT_LAYERS} == set(RUNTIME_CONTEXT_LAYER_KEYS)
    assert next(layer for layer in RUNTIME_CONTEXT_LAYERS if layer.key == RuntimeContextLayerKey.KNOWLEDGE).budget == "compress"
    assert next(
        layer for layer in RUNTIME_CONTEXT_LAYERS if layer.key == RuntimeContextLayerKey.SESSION_HISTORY
    ).child_task_behavior == "recompute_from_child_session"


def test_repo_instruction_discovery_is_missing_safe_and_child_cwd_specific(tmp_path: Path) -> None:
    from autocontext.session.runtime_context import (
        RuntimeContextDiscoveryRequest,
        discover_repo_instructions,
    )

    request = RuntimeContextDiscoveryRequest(workspace_root=tmp_path, cwd="/pkg")
    assert discover_repo_instructions(request) == ()

    (tmp_path / "AGENTS.md").write_text("root agents\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "CLAUDE.md").write_text("pkg claude\n", encoding="utf-8")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "AGENTS.md").write_text("other agents\n", encoding="utf-8")

    parent_instructions = discover_repo_instructions(request)
    child_instructions = discover_repo_instructions(request.for_child_task(cwd="/other"))

    assert tuple(instruction.relative_path for instruction in parent_instructions) == ("AGENTS.md", "pkg/CLAUDE.md")
    assert tuple(instruction.content for instruction in parent_instructions) == ("root agents\n", "pkg claude\n")
    assert tuple(instruction.relative_path for instruction in child_instructions) == ("AGENTS.md", "other/AGENTS.md")


def test_runtime_skill_discovery_is_cwd_specific_and_deduplicates(tmp_path: Path) -> None:
    from autocontext.session.runtime_context import RuntimeContextDiscoveryRequest, discover_runtime_skills

    root_skills = tmp_path / ".claude" / "skills"
    pkg_skills = tmp_path / "pkg" / ".claude" / "skills"
    _write_skill(root_skills, "shared", "root shared")
    _write_skill(root_skills, "root-only", "root only")
    pkg_shared = _write_skill(pkg_skills, "shared", "package shared")
    _write_skill(pkg_skills, "pkg-only", "package only")

    registry = discover_runtime_skills(RuntimeContextDiscoveryRequest(workspace_root=tmp_path, cwd="/pkg"))
    manifests = registry.all_manifests()

    assert [manifest.name for manifest in manifests] == ["pkg-only", "shared", "root-only"]
    assert registry.get("shared") is not None
    assert registry.get("shared").manifest.skill_path == pkg_shared

    root_registry = discover_runtime_skills(RuntimeContextDiscoveryRequest(workspace_root=tmp_path, cwd="/"))
    assert [manifest.name for manifest in root_registry.all_manifests()] == ["root-only", "shared"]


def test_knowledge_components_respect_include_exclude_and_empty_values() -> None:
    from autocontext.session.runtime_context import select_runtime_knowledge_components

    selected = select_runtime_knowledge_components(
        {
            "playbook": "Use validated strategy.",
            "hints": "",
            "lessons": "Lesson one.",
            "dead_ends": "Avoid stale path.",
            "private_notes": "do not include",
        },
        include=("playbook", "hints", "lessons", "dead_ends"),
        exclude=("lessons",),
    )

    assert selected == {
        "playbook": "Use validated strategy.",
        "dead_ends": "Avoid stale path.",
    }
