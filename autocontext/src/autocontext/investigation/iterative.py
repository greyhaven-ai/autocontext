from __future__ import annotations

from pathlib import Path
from typing import Any

from autocontext.agents.types import LlmFn
from autocontext.investigation.browser_context import (
    InvestigationBrowserContext,
    build_browser_evidence_summary,
    render_investigation_browser_context,
)
from autocontext.knowledge.compaction import (
    CompactionEntry,
    compact_prompt_components,
    compaction_entries_for_components,
)
from autocontext.prompts.context_budget import ContextBudget, estimate_tokens
from autocontext.scenarios.families import get_family_marker
from autocontext.util.json_io import write_json


def run_iterative_investigation(
    *,
    request: Any,
    investigation_id: str,
    name: str,
    analysis_llm_fn: LlmFn,
    knowledge_root: Path,
    artifacts: Any | None = None,
    events: Any | None = None,
    context_budget_tokens: int = 0,
    failed_result_fn: Any,
) -> Any:
    from autocontext.investigation.engine import (
        InvestigationArtifacts,
        InvestigationResult,
        normalize_positive_integer,
        parse_investigation_json,
    )

    try:
        max_steps = normalize_positive_integer(request.max_steps) or 8
        transcript: list[dict[str, Any]] = []
        latest_payload: dict[str, Any] = {}
        compaction_parent_id = _latest_compaction_parent_id(artifacts, investigation_id)

        _emit_investigation_event(
            events,
            "investigation_started",
            {
                "run_id": investigation_id,
                "scenario": name,
                "mode": "iterative",
                "max_steps": max_steps,
                "description": request.description,
            },
        )

        for step in range(1, max_steps + 1):
            _emit_investigation_event(
                events,
                "investigation_step_started",
                {
                    "run_id": investigation_id,
                    "scenario": name,
                    "mode": "iterative",
                    "generation": step,
                    "step": step,
                },
            )
            system_prompt, user_prompt = _build_iterative_investigation_prompt(
                description=request.description,
                step=step,
                max_steps=max_steps,
                transcript=transcript,
                browser_context=request.browser_context,
            )
            raw_response = analysis_llm_fn(system_prompt, user_prompt)
            parsed = parse_investigation_json(raw_response) or {}
            transcript.append(
                {
                    "step": step,
                    "system": system_prompt,
                    "user": user_prompt,
                    "response": raw_response,
                    "parsed": parsed,
                }
            )
            if parsed:
                latest_payload = parsed
            entries = _compact_iterative_context_if_needed(
                artifacts=artifacts,
                investigation_id=investigation_id,
                name=name,
                step=step,
                transcript=transcript,
                context_budget_tokens=context_budget_tokens,
                parent_id=compaction_parent_id,
            )
            if entries:
                compaction_parent_id = entries[-1].entry_id
            _emit_investigation_event(
                events,
                "investigation_step_completed",
                {
                    "run_id": investigation_id,
                    "scenario": name,
                    "mode": "iterative",
                    "generation": step,
                    "step": step,
                    "response_length": len(raw_response),
                    "transcript_tokens": estimate_tokens(_render_transcript_for_compaction(transcript)),
                    "compaction_entries": len(entries),
                },
            )

        investigation_dir = _persist_iterative_investigation_artifacts(knowledge_root, name, transcript, artifacts)
        hypotheses = _coerce_iterative_hypotheses(latest_payload, request.max_hypotheses)
        evidence = _coerce_iterative_evidence(latest_payload, browser_context=request.browser_context)
        conclusion = _coerce_iterative_conclusion(latest_payload, hypotheses)
        unknowns = _string_list(latest_payload.get("unknowns"))
        next_steps = _string_list(latest_payload.get("recommended_next_steps"))
        if not next_steps:
            next_steps = _recommend_next_steps(hypotheses, unknowns)
        report_path = investigation_dir / "report.json"
        result = InvestigationResult(
            id=investigation_id,
            name=name,
            family="investigation",
            status="completed",
            description=request.description,
            question=str(latest_payload.get("question") or request.description),
            hypotheses=hypotheses,
            evidence=evidence,
            conclusion=conclusion,
            unknowns=unknowns,
            recommended_next_steps=next_steps,
            steps_executed=max_steps,
            artifacts=InvestigationArtifacts(
                investigation_dir=str(investigation_dir),
                report_path=str(report_path),
            ),
        )
        _write_json_artifact(artifacts, report_path, result.to_dict())
        _emit_investigation_event(
            events,
            "investigation_completed",
            {
                "run_id": investigation_id,
                "scenario": name,
                "mode": "iterative",
                "status": "completed",
                "steps_executed": max_steps,
            },
        )
        return result
    except Exception as exc:
        _emit_investigation_event(
            events,
            "investigation_failed",
            {
                "run_id": investigation_id,
                "scenario": name,
                "mode": "iterative",
                "status": "failed",
                "error": str(exc),
            },
        )
        return failed_result_fn(
            investigation_id=investigation_id,
            name=name,
            request=request,
            errors=[str(exc)],
        )


def _build_iterative_investigation_prompt(
    *,
    description: str,
    step: int,
    max_steps: int,
    transcript: list[dict[str, Any]],
    browser_context: InvestigationBrowserContext | None = None,
) -> tuple[str, str]:
    system_prompt = (
        "You are running a live iterative investigation session. Each step should refine hypotheses, "
        "name evidence gathered so far, and identify what remains uncertain. Output ONLY JSON with keys: "
        "question, hypotheses, evidence, conclusion, unknowns, recommended_next_steps."
    )
    previous = []
    for item in transcript[-5:]:
        parsed = item.get("parsed")
        if isinstance(parsed, dict):
            conclusion = parsed.get("conclusion")
            if isinstance(conclusion, dict):
                previous.append(str(conclusion.get("best_explanation") or ""))
            elif conclusion:
                previous.append(str(conclusion))
    previous_summary = "\n".join(f"- {item}" for item in previous if item) or "- No prior step output."
    user_prompt = (
        f"Investigation: {description}\n"
        f"Step: {step} of {max_steps}\n"
        f"Previous step conclusions:\n{previous_summary}\n\n"
        "Return updated JSON. Hypotheses may include optional status values: supported, contradicted, unresolved. "
        "Evidence items may include id, kind, source, summary, supports, contradicts, and is_red_herring."
    )
    if browser_context is not None:
        user_prompt = f"{user_prompt}\n\n{render_investigation_browser_context(browser_context)}"
    return system_prompt, user_prompt


def _coerce_iterative_hypotheses(payload: dict[str, Any], max_hypotheses: int | None) -> list[Any]:
    from autocontext.investigation.engine import InvestigationHypothesis, normalize_positive_integer

    raw_hypotheses = payload.get("hypotheses")
    if not isinstance(raw_hypotheses, list):
        raw_hypotheses = [{"statement": str(payload.get("question") or "Investigate the reported issue")}]
    limit = normalize_positive_integer(max_hypotheses)
    if limit is not None:
        raw_hypotheses = raw_hypotheses[:limit]
    hypotheses: list[Any] = []
    for index, raw in enumerate(raw_hypotheses):
        if not isinstance(raw, dict):
            continue
        confidence = raw.get("confidence", 0.5)
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
        status = str(raw.get("status") or "unresolved")
        if status not in {"supported", "contradicted", "unresolved"}:
            status = "unresolved"
        hypotheses.append(
            InvestigationHypothesis(
                id=str(raw.get("id") or f"h{index}"),
                statement=str(raw.get("statement") or raw.get("hypothesis") or "Unspecified hypothesis"),
                status=status,
                confidence=max(0.0, min(1.0, float(confidence))),
            )
        )
    return hypotheses


def _coerce_iterative_evidence(
    payload: dict[str, Any],
    *,
    browser_context: InvestigationBrowserContext | None,
) -> list[Any]:
    from autocontext.investigation.engine import InvestigationEvidence

    evidence: list[Any] = []
    if browser_context is not None:
        evidence.append(
            InvestigationEvidence(
                id="browser_snapshot",
                kind="browser_snapshot",
                source=browser_context.url,
                summary=build_browser_evidence_summary(browser_context),
                is_red_herring=False,
            )
        )
    raw_evidence = payload.get("evidence")
    if not isinstance(raw_evidence, list):
        return evidence
    for index, raw in enumerate(raw_evidence):
        if not isinstance(raw, dict):
            continue
        supports = raw.get("supports")
        contradicts = raw.get("contradicts")
        evidence.append(
            InvestigationEvidence(
                id=str(raw.get("id") or f"e{index}"),
                kind=str(raw.get("kind") or "observation"),
                source=str(raw.get("source") or "iterative_session"),
                summary=str(raw.get("summary") or raw.get("content") or ""),
                supports=[str(item) for item in supports] if isinstance(supports, list) else [],
                contradicts=[str(item) for item in contradicts] if isinstance(contradicts, list) else [],
                is_red_herring=bool(raw.get("is_red_herring", False)),
            )
        )
    return evidence


def _coerce_iterative_conclusion(payload: dict[str, Any], hypotheses: list[Any]) -> Any:
    from autocontext.investigation.engine import InvestigationConclusion

    raw_conclusion = payload.get("conclusion")
    if isinstance(raw_conclusion, dict):
        confidence = raw_conclusion.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)):
            confidence = 0.0
        limitations = raw_conclusion.get("limitations")
        return InvestigationConclusion(
            best_explanation=str(raw_conclusion.get("best_explanation") or raw_conclusion.get("summary") or ""),
            confidence=max(0.0, min(1.0, float(confidence))),
            limitations=[str(item) for item in limitations] if isinstance(limitations, list) else [],
        )
    supported = sorted(
        [hypothesis for hypothesis in hypotheses if hypothesis.status == "supported"],
        key=lambda item: item.confidence,
        reverse=True,
    )
    best = supported[0] if supported else (hypotheses[0] if hypotheses else None)
    return InvestigationConclusion(
        best_explanation=best.statement if best else "No hypothesis received sufficient support",
        confidence=best.confidence if best else 0.0,
        limitations=["Iterative investigation based on LLM session transcript"],
    )


def _recommend_next_steps(hypotheses: list[Any], unknowns: list[str]) -> list[str]:
    steps: list[str] = []
    supported = [hypothesis for hypothesis in hypotheses if hypothesis.status == "supported"]
    if supported:
        steps.append(f'Verify leading hypothesis: "{supported[0].statement}"')
    for hypothesis in [item for item in hypotheses if item.status == "unresolved"][:2]:
        steps.append(f'Gather evidence for: "{hypothesis.statement}"')
    if unknowns:
        steps.append("Address identified unknowns before concluding")
    return steps


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _persist_iterative_investigation_artifacts(
    knowledge_root: Path,
    name: str,
    transcript: list[dict[str, Any]],
    artifacts: Any | None = None,
) -> Path:
    investigation_dir = knowledge_root / "_investigations" / name
    investigation_dir.mkdir(parents=True, exist_ok=True)
    _write_json_artifact(
        artifacts,
        investigation_dir / "spec.json",
        {
            "name": name,
            "family": "investigation",
            "mode": "iterative",
            "steps": len(transcript),
        },
    )
    _write_json_artifact(artifacts, investigation_dir / "transcript.json", {"steps": transcript})
    _write_text_artifact(artifacts, investigation_dir / "scenario_type.txt", get_family_marker("investigation"))
    return investigation_dir


def _compact_iterative_context_if_needed(
    *,
    artifacts: Any | None,
    investigation_id: str,
    name: str,
    step: int,
    transcript: list[dict[str, Any]],
    context_budget_tokens: int,
    parent_id: str,
) -> list[CompactionEntry]:
    if artifacts is None or context_budget_tokens <= 0:
        return []
    transcript_text = _render_transcript_for_compaction(transcript)
    tokens_before = estimate_tokens(transcript_text)
    if tokens_before <= context_budget_tokens:
        return []

    original = {"analysis": transcript_text}
    compacted = ContextBudget(max_tokens=context_budget_tokens).apply(compact_prompt_components(original))
    entries = compaction_entries_for_components(
        original,
        compacted,
        context={
            "scenario": name,
            "run_id": investigation_id,
            "mode": "iterative_investigation",
            "step": step,
            "trigger": "context_pressure",
            "context_budget_tokens": context_budget_tokens,
        },
        parent_id=parent_id,
    )
    if entries:
        artifacts.append_compaction_entries(investigation_id, entries)
    return entries


def _render_transcript_for_compaction(transcript: list[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for item in transcript:
        parsed = item.get("parsed") if isinstance(item, dict) else {}
        conclusion = parsed.get("conclusion") if isinstance(parsed, dict) else None
        if isinstance(conclusion, dict):
            conclusion_summary = str(conclusion.get("best_explanation") or conclusion.get("summary") or "")
        else:
            conclusion_summary = str(conclusion or "")
        chunks.append(
            "\n".join(
                [
                    f"## Step {item.get('step', '')}",
                    f"User prompt: {item.get('user', '')}",
                    f"Response: {item.get('response', '')}",
                    f"Conclusion: {conclusion_summary}",
                ]
            ).strip()
        )
    return "\n\n---\n\n".join(chunk for chunk in chunks if chunk)


def _latest_compaction_parent_id(artifacts: Any | None, investigation_id: str) -> str:
    if artifacts is None:
        return ""
    latest = getattr(artifacts, "latest_compaction_entry_id", None)
    return str(latest(investigation_id) or "") if callable(latest) else ""


def _emit_investigation_event(events: Any | None, event: str, payload: dict[str, Any]) -> None:
    if events is None:
        return
    events.emit(event, payload, channel="investigation")


def _write_json_artifact(artifacts: Any | None, path: Path, payload: dict[str, Any]) -> None:
    writer = getattr(artifacts, "write_json", None)
    if callable(writer):
        writer(path, payload)
        return
    write_json(path, payload)


def _write_text_artifact(artifacts: Any | None, path: Path, content: str) -> None:
    writer = getattr(artifacts, "write_markdown", None)
    if callable(writer):
        writer(path, content)
        return
    path.write_text(content, encoding="utf-8")
