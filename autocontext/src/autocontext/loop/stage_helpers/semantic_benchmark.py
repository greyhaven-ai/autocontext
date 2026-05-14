from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from autocontext.extensions import HookEvents
from autocontext.knowledge.compaction import CompactionEntry, prompt_compaction_cache_stats
from autocontext.knowledge.context_selection import build_prompt_context_selection_decision
from autocontext.knowledge.semantic_compaction_benchmark import (
    build_semantic_compaction_benchmark_report,
)
from autocontext.prompts.templates import PromptBundle, build_prompt_bundle
from autocontext.storage.context_selection_store import persist_context_selection_decision
from autocontext.util.json_io import write_json

if TYPE_CHECKING:
    from autocontext.loop.stage_types import GenerationContext
    from autocontext.scenarios.base import Observation
    from autocontext.storage import ArtifactStore

logger = logging.getLogger(__name__)


def _cache_counter_delta(before: Mapping[str, int], after: Mapping[str, int], key: str) -> int:
    return max(0, _coerce_cache_int(after.get(key)) - _coerce_cache_int(before.get(key)))


def _coerce_cache_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _prompt_compaction_cache_delta(before: Mapping[str, int], after: Mapping[str, int]) -> dict[str, int]:
    hits = _cache_counter_delta(before, after, "hits")
    misses = _cache_counter_delta(before, after, "misses")
    return {
        "hits": hits,
        "misses": misses,
        "lookups": hits + misses,
        "entries": _coerce_cache_int(after.get("entries")),
    }


def _latest_compaction_parent_id(artifacts: ArtifactStore, run_id: str) -> str:
    latest = getattr(artifacts, "latest_compaction_entry_id", None)
    if not callable(latest):
        return ""
    try:
        value = latest(run_id)
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


def _append_compaction_entries(artifacts: ArtifactStore, run_id: str, entries: list[CompactionEntry]) -> bool:
    append = getattr(artifacts, "append_compaction_entries", None)
    if not callable(append):
        return False
    append(run_id, entries)
    return True


def _append_compaction_entries_for_context(
    ctx: GenerationContext,
    artifacts: ArtifactStore,
    entries: list[CompactionEntry],
) -> None:
    if not _append_compaction_entries(artifacts, ctx.run_id, entries):
        return
    if not entries:
        return
    db_path = getattr(ctx.settings, "db_path", None)
    if db_path is None:
        return
    from autocontext.session.runtime_session import RuntimeSessionCompactionInput
    from autocontext.session.runtime_session_recording import create_runtime_session_for_run

    recording = create_runtime_session_for_run(
        db_path=db_path,
        run_id=ctx.run_id,
        scenario_name=ctx.scenario_name,
    )
    try:
        recording.session.record_compaction(
            RuntimeSessionCompactionInput(
                run_id=ctx.run_id,
                generation=ctx.generation,
                ledger_path=str(artifacts.compaction_ledger_path(ctx.run_id)),
                latest_entry_path=str(artifacts.compaction_latest_entry_path(ctx.run_id)),
                entries=[entry.to_dict() for entry in entries],
            )
        )
    finally:
        recording.close()


def _evidence_source_run_ids(ctx: GenerationContext, *, artifacts: ArtifactStore) -> list[str]:
    """Return prior same-scenario run ids with persisted knowledge snapshots."""
    snapshots_dir = artifacts.knowledge_root / ctx.scenario_name / "snapshots"
    if not snapshots_dir.is_dir():
        return []
    try:
        return sorted(
            path.name
            for path in snapshots_dir.iterdir()
            if path.is_dir() and path.name != ctx.run_id
        )
    except OSError:
        return []


def materialize_evidence_manifests(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> tuple[dict[str, str], Any]:
    """Build the evidence workspace and render role-specific prompt manifests."""
    from autocontext.evidence import materialize_workspace, render_evidence_manifest

    workspace = materialize_workspace(
        knowledge_root=artifacts.knowledge_root,
        runs_root=artifacts.runs_root,
        source_run_ids=_evidence_source_run_ids(ctx, artifacts=artifacts),
        workspace_dir=artifacts.knowledge_root / ctx.scenario_name / "_evidence",
        budget_bytes=ctx.settings.evidence_workspace_budget_mb * 1024 * 1024,
        scenario_name=ctx.scenario_name,
        scan_for_secrets=True,
    )
    return (
        {
            "analyst": render_evidence_manifest(workspace, role="analyst"),
            "architect": render_evidence_manifest(workspace, role="architect"),
        },
        workspace,
    )


def _benchmarkable_prompt_components(
    *,
    current_playbook: str,
    score_trajectory: str,
    operational_lessons: str,
    available_tools: str,
    recent_analysis: str,
    analyst_feedback: str,
    analyst_attribution: str,
    coach_attribution: str,
    architect_attribution: str,
    coach_competitor_hints: str,
    coach_hint_feedback: str,
    experiment_log: str,
    dead_ends: str,
    research_protocol: str,
    session_reports: str,
    architect_tool_usage_report: str,
    environment_snapshot: str,
    evidence_manifest: str,
    evidence_manifests: dict[str, str] | None,
    notebook_contexts: dict[str, str] | None,
) -> dict[str, str]:
    """Collect prompt-facing context components for benchmarking and observability."""
    _evidence = dict(evidence_manifests or {})
    _nb = dict(notebook_contexts or {})
    return {
        "playbook": current_playbook,
        "trajectory": score_trajectory,
        "lessons": operational_lessons,
        "tools": available_tools,
        "analysis": recent_analysis,
        "analyst_feedback": analyst_feedback,
        "analyst_attribution": analyst_attribution,
        "coach_attribution": coach_attribution,
        "architect_attribution": architect_attribution,
        "hints": coach_competitor_hints,
        "coach_hint_feedback": coach_hint_feedback,
        "experiment_log": experiment_log,
        "dead_ends": dead_ends,
        "research_protocol": research_protocol,
        "session_reports": session_reports,
        "tool_usage_report": architect_tool_usage_report,
        "environment_snapshot": environment_snapshot,
        "evidence_manifest": evidence_manifest,
        "evidence_manifest_analyst": _evidence.get("analyst", evidence_manifest),
        "evidence_manifest_architect": _evidence.get("architect", evidence_manifest),
        "notebook_competitor": _nb.get("competitor", ""),
        "notebook_analyst": _nb.get("analyst", ""),
        "notebook_coach": _nb.get("coach", ""),
        "notebook_architect": _nb.get("architect", ""),
    }


def _benchmarkable_prompt_components_from_kwargs(prompt_kwargs: dict[str, Any]) -> dict[str, str]:
    return _benchmarkable_prompt_components(
        current_playbook=_as_str(prompt_kwargs.get("current_playbook")),
        score_trajectory=_as_str(prompt_kwargs.get("score_trajectory")),
        operational_lessons=_as_str(prompt_kwargs.get("operational_lessons")),
        available_tools=_as_str(prompt_kwargs.get("available_tools")),
        recent_analysis=_as_str(prompt_kwargs.get("recent_analysis")),
        analyst_feedback=_as_str(prompt_kwargs.get("analyst_feedback")),
        analyst_attribution=_as_str(prompt_kwargs.get("analyst_attribution")),
        coach_attribution=_as_str(prompt_kwargs.get("coach_attribution")),
        architect_attribution=_as_str(prompt_kwargs.get("architect_attribution")),
        coach_competitor_hints=_as_str(prompt_kwargs.get("coach_competitor_hints")),
        coach_hint_feedback=_as_str(prompt_kwargs.get("coach_hint_feedback")),
        experiment_log=_as_str(prompt_kwargs.get("experiment_log")),
        dead_ends=_as_str(prompt_kwargs.get("dead_ends")),
        research_protocol=_as_str(prompt_kwargs.get("research_protocol")),
        session_reports=_as_str(prompt_kwargs.get("session_reports")),
        architect_tool_usage_report=_as_str(prompt_kwargs.get("architect_tool_usage_report")),
        environment_snapshot=_as_str(prompt_kwargs.get("environment_snapshot")),
        evidence_manifest=_as_str(prompt_kwargs.get("evidence_manifest")),
        evidence_manifests=_as_str_dict(prompt_kwargs.get("evidence_manifests")),
        notebook_contexts=_as_str_dict(prompt_kwargs.get("notebook_contexts")),
    )


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _as_str_dict(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    return {
        str(key): str(item)
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str)
    }


def prepare_generation_prompts(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
    scenario_rules: str,
    strategy_interface: str,
    evaluation_criteria: str,
    previous_summary: str,
    observation: Observation,
    current_playbook: str,
    available_tools: str,
    operational_lessons: str,
    replay_narrative: str,
    coach_competitor_hints: str,
    coach_hint_feedback: str,
    recent_analysis: str,
    analyst_feedback: str,
    analyst_attribution: str,
    coach_attribution: str,
    architect_attribution: str,
    score_trajectory: str,
    strategy_registry: str,
    progress_json: str,
    experiment_log: str,
    dead_ends: str,
    research_protocol: str,
    session_reports: str,
    architect_tool_usage_report: str,
    constraint_mode: bool,
    context_budget_tokens: int,
    notebook_contexts: dict[str, str] | None,
    environment_snapshot: str,
    evidence_manifest: str,
    evidence_manifests: dict[str, str] | None,
    evidence_cache_hits: int,
    evidence_cache_lookups: int,
) -> tuple[PromptBundle, dict[str, Any] | None]:
    prompt_kwargs: dict[str, Any] = {
        "scenario_rules": scenario_rules,
        "strategy_interface": strategy_interface,
        "evaluation_criteria": evaluation_criteria,
        "previous_summary": previous_summary,
        "observation": observation,
        "current_playbook": current_playbook,
        "available_tools": available_tools,
        "operational_lessons": operational_lessons,
        "replay_narrative": replay_narrative,
        "coach_competitor_hints": coach_competitor_hints,
        "coach_hint_feedback": coach_hint_feedback,
        "recent_analysis": recent_analysis,
        "analyst_feedback": analyst_feedback,
        "analyst_attribution": analyst_attribution,
        "coach_attribution": coach_attribution,
        "architect_attribution": architect_attribution,
        "score_trajectory": score_trajectory,
        "strategy_registry": strategy_registry,
        "progress_json": progress_json,
        "experiment_log": experiment_log,
        "dead_ends": dead_ends,
        "research_protocol": research_protocol,
        "session_reports": session_reports,
        "architect_tool_usage_report": architect_tool_usage_report,
        "constraint_mode": constraint_mode,
        "context_budget_tokens": context_budget_tokens,
        "notebook_contexts": notebook_contexts,
        "environment_snapshot": environment_snapshot,
        "evidence_manifest": evidence_manifest,
        "evidence_manifests": evidence_manifests,
    }
    hook_bus = ctx.hook_bus
    if hook_bus is not None:
        context_components = hook_bus.emit(
            HookEvents.CONTEXT_COMPONENTS,
            {
                "components": dict(prompt_kwargs),
                "scenario_name": ctx.scenario_name,
                "run_id": ctx.run_id,
                "generation": ctx.generation,
            },
        )
        context_components.raise_if_blocked()
        maybe_components = context_components.payload.get("components")
        if isinstance(maybe_components, dict):
            prompt_kwargs.update(maybe_components)
        prompt_kwargs["hook_bus"] = hook_bus
    prompt_kwargs["compaction_entry_context"] = {
        "scenario": ctx.scenario_name,
        "run_id": ctx.run_id,
        "generation": ctx.generation,
    }
    prompt_kwargs["compaction_entry_parent_id"] = _latest_compaction_parent_id(artifacts, ctx.run_id)
    prompt_kwargs["compaction_entry_sink"] = lambda entries: _append_compaction_entries_for_context(ctx, artifacts, entries)
    raw_context_components = _benchmarkable_prompt_components_from_kwargs(prompt_kwargs)
    selected_context_components: dict[str, str] = {}
    context_budget_telemetry: list[dict[str, Any]] = []
    prompt_kwargs["context_component_sink"] = selected_context_components.update
    prompt_kwargs["context_budget_telemetry_sink"] = lambda telemetry: context_budget_telemetry.append(telemetry.to_dict())
    compaction_cache_before = prompt_compaction_cache_stats()
    build_start = time.perf_counter()
    prompts = build_prompt_bundle(**prompt_kwargs)
    semantic_build_latency_ms = (time.perf_counter() - build_start) * 1000.0
    compaction_cache_after = prompt_compaction_cache_stats()
    _persist_generation_context_selection(
        ctx,
        artifacts=artifacts,
        candidate_components=raw_context_components,
        selected_components=selected_context_components,
        context_budget_tokens=context_budget_tokens,
        context_budget_telemetry=context_budget_telemetry[-1] if context_budget_telemetry else None,
        prompt_compaction_cache=_prompt_compaction_cache_delta(compaction_cache_before, compaction_cache_after),
        evidence_cache_hits=evidence_cache_hits,
        evidence_cache_lookups=evidence_cache_lookups,
    )
    if not ctx.settings.semantic_compaction_benchmark_enabled:
        return prompts, None

    baseline_start = time.perf_counter()
    baseline_prompt_kwargs = dict(prompt_kwargs)
    baseline_prompt_kwargs.pop("context_component_sink", None)
    baseline_prompt_kwargs.pop("context_budget_telemetry_sink", None)
    budget_only_prompts = build_prompt_bundle(**baseline_prompt_kwargs, semantic_compaction=False)
    budget_only_build_latency_ms = (time.perf_counter() - baseline_start) * 1000.0
    benchmark_report = build_semantic_compaction_benchmark_report(
        scenario_name=ctx.scenario_name,
        run_id=ctx.run_id,
        generation=ctx.generation,
        context_budget_tokens=context_budget_tokens,
        raw_components=raw_context_components,
        semantic_prompts=prompts,
        budget_only_prompts=budget_only_prompts,
        semantic_build_latency_ms=semantic_build_latency_ms,
        budget_only_build_latency_ms=budget_only_build_latency_ms,
        evidence_cache_hits=evidence_cache_hits,
        evidence_cache_lookups=evidence_cache_lookups,
    )
    report_payload = benchmark_report.to_dict()
    report_path = (
        artifacts.knowledge_root
        / ctx.scenario_name
        / "semantic_compaction_reports"
        / f"{ctx.run_id}_gen_{ctx.generation}.json"
    )
    write_json(report_path, report_payload)
    return prompts, report_payload


def _persist_generation_context_selection(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
    candidate_components: dict[str, str],
    selected_components: dict[str, str],
    context_budget_tokens: int,
    context_budget_telemetry: dict[str, Any] | None,
    prompt_compaction_cache: Mapping[str, int],
    evidence_cache_hits: int,
    evidence_cache_lookups: int,
) -> None:
    metadata: dict[str, Any] = {
        "context_budget_tokens": context_budget_tokens,
        "semantic_compaction": True,
        "selected_component_scope": "final_role_prompts_after_context_hook",
        "prompt_compaction_cache": dict(prompt_compaction_cache),
        "evidence_cache_hits": evidence_cache_hits,
        "evidence_cache_lookups": evidence_cache_lookups,
    }
    if context_budget_telemetry is not None:
        metadata["context_budget_telemetry"] = dict(context_budget_telemetry)
    decision = build_prompt_context_selection_decision(
        run_id=ctx.run_id,
        scenario_name=ctx.scenario_name,
        generation=ctx.generation,
        stage="generation_prompt_context",
        candidate_components=candidate_components,
        selected_components=selected_components,
        metadata=metadata,
    )
    try:
        persist_context_selection_decision(artifacts, decision)
    except Exception:
        logger.debug("failed to persist context selection decision", exc_info=True)
