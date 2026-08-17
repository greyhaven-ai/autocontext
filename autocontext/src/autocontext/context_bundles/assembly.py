"""Assemble and resolve complete context bundles from live loop artifacts."""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Protocol

from autocontext.context_bundles.models import BundleComponent, ComponentKind, ContextBundle
from autocontext.harness.mutations import HarnessMutation, evaluate_mutation


class LegacyContextReader(Protocol):
    def read_playbook(self, scenario_name: str) -> str: ...
    def read_hints(self, scenario_name: str) -> str: ...
    def read_tool_context(self, scenario_name: str) -> str: ...
    def read_harness_context(self, scenario_name: str) -> str: ...
    def list_harness(self, scenario_name: str) -> list[str]: ...
    def read_harness(self, scenario_name: str, name: str) -> str | None: ...
    def load_harness_mutations(self, scenario_name: str) -> list[HarnessMutation]: ...


def build_legacy_baseline(
    artifacts: LegacyContextReader,
    scenario: str,
    *,
    evaluator_epoch: str,
    routing_config: Mapping[str, Any] | None = None,
) -> ContextBundle:
    """Capture the pre-bundle live state once as a migration baseline."""
    components = [
        BundleComponent(ComponentKind.PLAYBOOK, "playbook", artifacts.read_playbook(scenario), "text/markdown"),
        BundleComponent(ComponentKind.HINTS, "hints", artifacts.read_hints(scenario), "text/markdown"),
        BundleComponent(
            ComponentKind.TOOL_GUIDANCE,
            "legacy_tool_context",
            artifacts.read_tool_context(scenario),
            "text/markdown",
        ),
        BundleComponent.json(ComponentKind.ROUTING_CONFIG, "roles", dict(routing_config or {})),
    ]
    harness_names = artifacts.list_harness(scenario)
    for name in harness_names:
        source = artifacts.read_harness(scenario, name)
        if source:
            components.append(BundleComponent(ComponentKind.HARNESS_VALIDATOR, name, source, "text/x-python"))
    if not harness_names:
        components.append(
            BundleComponent(
                ComponentKind.HARNESS_VALIDATOR,
                "legacy_harness_context",
                artifacts.read_harness_context(scenario),
                "text/markdown",
            )
        )
    components.extend(_mutation_components(artifacts.load_harness_mutations(scenario)))
    return ContextBundle.create(
        scenario=scenario,
        evaluator_epoch=evaluator_epoch,
        components=components,
    )


def build_candidate_bundle(
    active: ContextBundle,
    *,
    evaluator_epoch: str,
    playbook: str = "",
    hints: str = "",
    mutations: Iterable[HarnessMutation] = (),
    tool_specs: Iterable[dict[str, Any]] = (),
    harness_specs: Iterable[dict[str, Any]] = (),
    routing_config: Mapping[str, Any] | None = None,
) -> ContextBundle | None:
    """Overlay structurally valid proposals without mutating the active bundle."""
    by_identity = {(component.kind, component.key): component for component in active.components}
    if playbook.strip():
        by_identity[(ComponentKind.PLAYBOOK, "playbook")] = BundleComponent(
            ComponentKind.PLAYBOOK,
            "playbook",
            playbook.strip(),
            "text/markdown",
        )
    if hints.strip():
        by_identity[(ComponentKind.HINTS, "hints")] = BundleComponent(
            ComponentKind.HINTS,
            "hints",
            hints.strip(),
            "text/markdown",
        )
    for component in _mutation_components(mutations):
        by_identity[(component.kind, component.key)] = component
    for index, spec in enumerate(tool_specs):
        validated = _validated_code_spec(spec, kind="tool", index=index)
        if validated is not None:
            by_identity[(validated.kind, validated.key)] = validated
    for index, spec in enumerate(harness_specs):
        validated = _validated_code_spec(spec, kind="harness", index=index)
        if validated is not None:
            by_identity[(validated.kind, validated.key)] = validated
    if routing_config is not None:
        by_identity[(ComponentKind.ROUTING_CONFIG, "roles")] = BundleComponent.json(
            ComponentKind.ROUTING_CONFIG,
            "roles",
            dict(routing_config),
        )

    candidate = ContextBundle.create(
        scenario=active.scenario,
        evaluator_epoch=evaluator_epoch,
        parent_digest=active.digest,
        components=list(by_identity.values()),
    )
    active_content = [(component.kind, component.key, component.digest) for component in active.components]
    candidate_content = [(component.kind, component.key, component.digest) for component in candidate.components]
    if active_content == candidate_content and active.evaluator_epoch == candidate.evaluator_epoch:
        return None
    return candidate


def bundle_text(bundle: ContextBundle, kind: ComponentKind, key: str, *, default: str = "") -> str:
    for component in bundle.components:
        if component.kind == kind and component.key == key:
            return component.content
    return default


def bundle_mutations(bundle: ContextBundle) -> list[HarnessMutation]:
    mutations: list[HarnessMutation] = []
    mutation_kinds = {
        ComponentKind.PROMPT_FRAGMENT,
        ComponentKind.CONTEXT_POLICY,
        ComponentKind.COMPLETION_CHECK,
        ComponentKind.TOOL_GUIDANCE,
    }
    for component in bundle.components:
        if component.kind not in mutation_kinds or component.media_type != "application/json":
            continue
        try:
            value = json.loads(component.content)
            if isinstance(value, dict) and "type" in value:
                mutation = HarnessMutation.from_dict(value)
                mutation.active = True
                mutations.append(mutation)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
    return mutations


def bundle_tool_context(bundle: ContextBundle) -> str:
    sections: list[str] = []
    for component in bundle.components:
        if component.kind == ComponentKind.TOOL_GUIDANCE and component.media_type != "application/json":
            if component.content.strip():
                sections.append(component.content.strip())
        elif component.kind == ComponentKind.TOOL_SPEC:
            try:
                spec = json.loads(component.content)
            except json.JSONDecodeError:
                continue
            if not isinstance(spec, dict):
                continue
            description = str(spec.get("description", "")).strip()
            code = str(spec.get("code", "")).strip()
            sections.append(f"### Tool: {component.key}\n{description}\n```python\n{code}\n```")
    return "\n\n".join(sections) or "No generated tools available."


def routing_snapshot(settings: Any) -> dict[str, Any]:
    """Capture only prompt/harness-affecting routes, never credentials."""
    fields = (
        "agent_provider",
        "model_competitor",
        "model_analyst",
        "model_coach",
        "model_architect",
        "judge_provider",
        "judge_model",
        "structural_role_isolation",
        "harness_validators_enabled",
    )
    return {field: getattr(settings, field) for field in fields if hasattr(settings, field)}


def evaluator_epoch_for(scenario: Any, settings: Any) -> str:
    """Compute the exact evaluator identity used for bundle comparisons."""
    from autocontext.execution.evaluator_epoch import compute_evaluator_epoch
    from autocontext.providers.registry import resolve_auto_judge_provider

    provider = str(getattr(settings, "judge_provider", "auto"))
    if provider == "auto":
        provider = resolve_auto_judge_provider(settings)
    rubric = scenario.describe_evaluation_criteria()
    if not isinstance(rubric, str):
        rubric = ""
    return compute_evaluator_epoch(
        rubric,
        provider,
        str(getattr(settings, "judge_model", "")),
    ).epoch_id


def _mutation_components(mutations: Iterable[HarnessMutation]) -> list[BundleComponent]:
    components: list[BundleComponent] = []
    kind_by_type = {
        "prompt_fragment": ComponentKind.PROMPT_FRAGMENT,
        "context_policy": ComponentKind.CONTEXT_POLICY,
        "completion_check": ComponentKind.COMPLETION_CHECK,
        "tool_instruction": ComponentKind.TOOL_GUIDANCE,
    }
    for mutation in mutations:
        if not isinstance(mutation, HarnessMutation) or not evaluate_mutation(mutation).approved:
            continue
        data = mutation.to_dict()
        data["active"] = True
        components.append(
            BundleComponent.json(
                kind_by_type[mutation.mutation_type.value],
                f"mutation:{mutation.mutation_id}",
                data,
            )
        )
    return components


def _validated_code_spec(spec: dict[str, Any], *, kind: str, index: int) -> BundleComponent | None:
    if not isinstance(spec, dict):
        return None
    raw_name = spec.get("name")
    raw_code = spec.get("code")
    if not isinstance(raw_name, str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", raw_name.strip()):
        return None
    if not isinstance(raw_code, str) or not raw_code.strip():
        return None
    try:
        ast.parse(raw_code)
    except SyntaxError:
        return None
    component_kind = ComponentKind.TOOL_SPEC if kind == "tool" else ComponentKind.HARNESS_VALIDATOR
    return BundleComponent.json(component_kind, raw_name.strip() or f"{kind}_{index}", spec)
