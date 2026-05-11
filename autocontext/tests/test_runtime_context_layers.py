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


def test_runtime_context_assembler_materializes_ordered_bundle_with_provenance(tmp_path: Path) -> None:
    from autocontext.session.runtime_context import (
        RUNTIME_CONTEXT_LAYER_KEYS,
        RuntimeContextAssemblyRequest,
        RuntimeContextDiscoveryRequest,
        RuntimeContextLayerKey,
        assemble_runtime_context,
    )

    (tmp_path / "AGENTS.md").write_text("root agents\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "CLAUDE.md").write_text("pkg claude\n", encoding="utf-8")
    _write_skill(tmp_path / "pkg" / ".claude" / "skills", "shared", "package shared")

    bundle = assemble_runtime_context(
        RuntimeContextAssemblyRequest(
            discovery=RuntimeContextDiscoveryRequest(workspace_root=tmp_path, cwd="/pkg"),
            system_policy="System policy text.",
            role_instructions="Role instruction text.",
            scenario_context="Scenario context text.",
            knowledge_components={
                "playbook": "Use validated strategy.",
                "lessons": "Excluded lesson.",
                "empty": "",
                "private_notes": "do not include",
            },
            knowledge_include=("playbook", "lessons", "empty"),
            knowledge_exclude=("lessons",),
            tool_affordances={"shell": "Workspace shell grant."},
            session_history=("Recent compacted turn.",),
        )
    )

    assert tuple(layer.layer.key for layer in bundle.layers) == RUNTIME_CONTEXT_LAYER_KEYS
    assert [entry.title for entry in bundle.all_entries()] == [
        "System Policy",
        "AGENTS.md",
        "pkg/CLAUDE.md",
        "Role Instructions",
        "Scenario Context",
        "playbook",
        "shared",
        "shell",
        "Recent Session History",
    ]

    repo_entries = bundle.get_layer(RuntimeContextLayerKey.REPO_INSTRUCTIONS).entries
    assert repo_entries[1].provenance["relative_path"] == "pkg/CLAUDE.md"
    assert repo_entries[1].provenance["source_type"] == "repo_instruction"

    skill_entry = bundle.get_layer(RuntimeContextLayerKey.RUNTIME_SKILLS).entries[0]
    assert skill_entry.provenance["source_type"] == "runtime_skill"
    assert skill_entry.metadata["manifest_first"] == "true"
    assert skill_entry.content == "package shared"


def test_runtime_context_assembler_recomputes_workspace_layers_for_child_cwd(tmp_path: Path) -> None:
    from autocontext.session.runtime_context import (
        RuntimeContextAssemblyRequest,
        RuntimeContextDiscoveryRequest,
        RuntimeContextLayerKey,
        assemble_runtime_context,
    )

    (tmp_path / "AGENTS.md").write_text("root agents\n", encoding="utf-8")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "AGENTS.md").write_text("pkg agents\n", encoding="utf-8")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "CLAUDE.md").write_text("other claude\n", encoding="utf-8")
    _write_skill(tmp_path / "pkg" / ".claude" / "skills", "pkg-only", "package skill")
    _write_skill(tmp_path / "other" / ".claude" / "skills", "other-only", "other skill")

    request = RuntimeContextAssemblyRequest(
        discovery=RuntimeContextDiscoveryRequest(workspace_root=tmp_path, cwd="/pkg"),
        role_instructions="same role text",
    )

    parent = assemble_runtime_context(request)
    child = assemble_runtime_context(request.for_child_task("/other"))

    parent_repo_entries = parent.get_layer(RuntimeContextLayerKey.REPO_INSTRUCTIONS).entries
    child_repo_entries = child.get_layer(RuntimeContextLayerKey.REPO_INSTRUCTIONS).entries

    assert [entry.provenance["relative_path"] for entry in parent_repo_entries] == [
        "AGENTS.md",
        "pkg/AGENTS.md",
    ]
    assert [entry.title for entry in parent.get_layer(RuntimeContextLayerKey.RUNTIME_SKILLS).entries] == ["pkg-only"]
    assert [entry.provenance["relative_path"] for entry in child_repo_entries] == [
        "AGENTS.md",
        "other/CLAUDE.md",
    ]
    assert [entry.title for entry in child.get_layer(RuntimeContextLayerKey.RUNTIME_SKILLS).entries] == ["other-only"]
    assert [entry.content for entry in child.get_layer(RuntimeContextLayerKey.ROLE_INSTRUCTIONS).entries] == [
        "same role text"
    ]
