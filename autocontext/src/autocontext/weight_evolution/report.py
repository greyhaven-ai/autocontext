"""Deterministic JSON and Markdown views of independently replayed evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.util.json_io import write_text_atomic
from autocontext.weight_evolution.evaluation import ReproductionEvaluation


def _percent(value: float | None) -> str:
    return "unavailable" if value is None else f"{100 * value:.3f}%"


def render_reproduction_report(evaluation: ReproductionEvaluation) -> str:
    report = evaluation.to_dict()
    title = "fixture report" if report["evidence_kind"] == "fixture" else "reproduction report"
    lines = [
        f"# Qwen base-versus-patch {title}", "",
        f"Evidence classification: **{report['evidence_kind']}**. Saved evidence coverage is complete for the frozen plan.",
        "", f"Plan digest: `{report['plan_digest']}`.",
        f"Report content digest: `{evaluation.digest}`.", "",
        "This report compares frozen checkpoints. It does not claim autonomous discovery, deployment readiness, or promotion.",
        "", "## Accuracy and generation cost", "",
        "| Dataset | Problems | Base accuracy | Patch accuracy | Paired change | 95% paired interval |",
        "| --- | ---: | ---: | ---: | ---: | --- |",
    ]
    comparisons: list[dict[str, Any]] = report["comparisons"]
    for comparison in comparisons:
        base, patch, effect = comparison["base"], comparison["published_patch"], comparison["paired_effect"]
        interval = f"[{_percent(effect['lower'])}, {_percent(effect['upper'])}]"
        lines.append(
            f"| {comparison['dataset']} | {base['n_problems']} | {_percent(base['mean_sample_accuracy'])} | "
            f"{_percent(patch['mean_sample_accuracy'])} | {_percent(effect['mean'])} | {interval} |"
        )
    lines.extend([
        "", "Accuracy averages samples within each problem, then averages problems; it is not pass@k.",
        "Intervals use paired per-problem effects and a Student-t model, with one look per benchmark. "
        "They are descriptive, not familywise or promotion guarantees. Small or zero-variance cohorts need caution.",
        "", "| Dataset | Variant | Samples | Token-limit rate | Mean generated tokens | p95 tokens | Worker seconds |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ])
    for comparison in comparisons:
        for variant in ("base", "published_patch"):
            metrics = comparison[variant]
            lines.append(
                f"| {comparison['dataset']} | {variant} | {metrics['n_samples']} | {_percent(metrics['token_limit_rate'])} | "
                f"{metrics['mean_generated_tokens']:.2f} | {metrics['p95_generated_tokens']} | "
                f"{metrics['worker_elapsed_seconds']:.3f} |"
            )
    lines.extend(["", "## Published-result comparison", "",
                  "Published values are the pinned final aggregate records, independent of this measured evidence.", "",
                  "| Dataset | Variant | Published accuracy | Measured minus published | Comparison scope |",
                  "| --- | --- | ---: | ---: | --- |"])
    for comparison in comparisons:
        source = comparison["published_comparison"]
        for variant in ("base", "published_patch"):
            metric = source[variant]
            lines.append(f"| {comparison['dataset']} | {variant} | {_percent(metric['accuracy'])} | "
                         f"{_percent(metric['accuracy_difference'])} | {source['scope']} |")
    lines.extend(["", "Published sources: " + ", ".join(
        f"[{source['variant']}]({source['url']})" for source in report["published_reference"]["sources"]
    ) + "."])
    lines.extend(["", "## Provenance", "", f"Runtime image: `{report['runtime_image']}`.",
                  f"Dataset lock: `{report['dataset_lock_digest']}`.",
                  f"Scorer lock: `{report['scorer_lock_digest']}`.",
                  f"Inference harness: `{report['harness_digest']}`.", "",
                  "The JSON report retains checkpoint identities, exact runtime/hardware observations, every sample's "
                  "raw-report/response hash, and every shard receipt identity. Raw response text remains in the worker reports.",
                  "", "## Fidelity and remaining claims", ""])
    lines.extend(f"- {note}" for note in report["fidelity_notes"])
    if report["evidence_kind"] != "full_cohort_replay":
        lines.extend(["", "This is fixture, smoke, or subset evidence; it cannot establish full benchmark reproduction."])
    return "\n".join(lines) + "\n"


def write_reproduction_report(evaluation: ReproductionEvaluation, output_dir: Path) -> dict[str, str]:
    """Publish a new report directory; a final manifest marks completion."""
    if output_dir.is_symlink() or output_dir.exists():
        raise ValueError("report output must be a new directory")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir()
    payload = evaluation.to_dict()
    write_text_atomic(output_dir / "report.json", json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n")
    write_text_atomic(output_dir / "report.md", render_reproduction_report(evaluation))
    manifest = {"schema_version": 1, "report_digest": evaluation.digest, "plan_digest": payload["plan_digest"]}
    write_text_atomic(output_dir / "complete.json", json.dumps(manifest, sort_keys=True, indent=2) + "\n")
    return {"report_digest": evaluation.digest, "json": str(output_dir / "report.json"),
            "markdown": str(output_dir / "report.md")}
