from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from autocontext.knowledge.semantic_compaction_benchmark import (
    build_semantic_compaction_benchmark_report,
)
from autocontext.prompts.templates import PromptBundle, build_prompt_bundle
from autocontext.util.json_io import write_json

if TYPE_CHECKING:
    from autocontext.loop.stage_types import GenerationContext
    from autocontext.scenarios.base import Observation
    from autocontext.storage import ArtifactStore


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
    build_start = time.perf_counter()
    prompts = build_prompt_bundle(**prompt_kwargs)
    semantic_build_latency_ms = (time.perf_counter() - build_start) * 1000.0
    if not ctx.settings.semantic_compaction_benchmark_enabled:
        return prompts, None

    baseline_start = time.perf_counter()
    budget_only_prompts = build_prompt_bundle(**prompt_kwargs, semantic_compaction=False)
    budget_only_build_latency_ms = (time.perf_counter() - baseline_start) * 1000.0
    benchmark_report = build_semantic_compaction_benchmark_report(
        scenario_name=ctx.scenario_name,
        run_id=ctx.run_id,
        generation=ctx.generation,
        context_budget_tokens=ctx.settings.context_budget_tokens,
        raw_components=_benchmarkable_prompt_components(
            current_playbook=current_playbook,
            score_trajectory=score_trajectory,
            operational_lessons=operational_lessons,
            available_tools=available_tools,
            recent_analysis=recent_analysis,
            analyst_feedback=analyst_feedback,
            analyst_attribution=analyst_attribution,
            coach_attribution=coach_attribution,
            architect_attribution=architect_attribution,
            coach_competitor_hints=coach_competitor_hints,
            coach_hint_feedback=coach_hint_feedback,
            experiment_log=experiment_log,
            dead_ends=dead_ends,
            research_protocol=research_protocol,
            session_reports=session_reports,
            architect_tool_usage_report=architect_tool_usage_report,
            environment_snapshot=environment_snapshot,
            evidence_manifest=evidence_manifest,
            evidence_manifests=evidence_manifests,
            notebook_contexts=notebook_contexts,
        ),
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
