"""Simulation export — portable result packages (AC-453, parity with TS AC-452)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.util.json_io import read_json, write_json


def export_simulation(
    id: str,
    knowledge_root: Path,
    format: str = "json",
) -> dict[str, Any]:
    """Export a saved simulation as a portable package."""
    normalized_format = format.lower()
    if normalized_format not in {"json", "markdown", "csv"}:
        return {
            "status": "failed",
            "error": f"Unsupported export format '{format}'. Use json, markdown, or csv.",
            "format": normalized_format,
        }

    resolved = _resolve_simulation_artifact(knowledge_root, id)
    if resolved is None:
        return {"status": "failed", "error": f"Simulation '{id}' not found", "format": normalized_format}

    report, sim_dir = resolved
    spec_path = sim_dir / "spec.json"
    spec = read_json(spec_path) if spec_path.exists() else {}

    output_dir = sim_dir / "exports"
    output_dir.mkdir(parents=True, exist_ok=True)

    if normalized_format == "markdown":
        return _export_markdown(report, spec, output_dir)
    if normalized_format == "csv":
        return _export_csv(report, output_dir)
    return _export_json(report, spec, output_dir)


def _resolve_simulation_artifact(knowledge_root: Path, simulation_id: str) -> tuple[dict[str, Any], Path] | None:
    simulations_root = knowledge_root / "_simulations"
    report_path = simulations_root / simulation_id / "report.json"
    if report_path.exists():
        return read_json(report_path), report_path.parent

    if not simulations_root.exists():
        return None

    for sim_dir in simulations_root.iterdir():
        if not sim_dir.is_dir() or sim_dir.name.startswith("_"):
            continue
        replay_path = sim_dir / f"replay_{simulation_id}.json"
        if replay_path.exists():
            return read_json(replay_path), sim_dir

    return None


def _export_stem(report: dict[str, Any]) -> str:
    if report.get("replay_of"):
        return f"replay_{report.get('id', 'simulation')}"
    return str(report.get("name", "simulation"))


def _collect_dimension_keys(report: dict[str, Any]) -> list[str]:
    keys = set((report.get("summary", {}) or {}).get("dimension_scores", {}).keys())
    for row in (report.get("sweep", {}) or {}).get("results", []):
        keys.update((row.get("dimension_scores", {}) or {}).keys())
    return sorted(keys)


def _collect_variable_keys(report: dict[str, Any]) -> list[str]:
    keys = set((report.get("variables") or {}).keys())
    for row in (report.get("sweep", {}) or {}).get("results", []):
        keys.update((row.get("variables", {}) or {}).keys())
    return sorted(keys)


def _stringify_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def _export_json(report: dict[str, Any], spec: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    pkg = {
        "id": report.get("id", ""),
        "name": report.get("name", ""),
        "family": report.get("family", "simulation"),
        "description": report.get("description", ""),
        "spec": spec,
        "variables": report.get("variables", {}),
        "results": report.get("summary", {}),
        "execution": report.get("execution", {}),
        "sweep": report.get("sweep"),
        "assumptions": report.get("assumptions", []),
        "warnings": report.get("warnings", []),
        "replay_of": report.get("replay_of"),
        "original_score": report.get("original_score"),
        "score_delta": report.get("score_delta"),
    }
    path = output_dir / f"{_export_stem(report)}_export.json"
    write_json(path, pkg)
    return {"status": "completed", "format": "json", "output_path": str(path)}


def _export_markdown(report: dict[str, Any], spec: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    name = report.get("name", "simulation")
    lines = [
        f"# Simulation Report: {name}",
        "",
        f"**Family:** {report.get('family', 'simulation')}",
        f"**Status:** {report.get('status', 'unknown')}",
        f"**Description:** {report.get('description', '')}",
    ]
    if report.get("replay_of"):
        lines.append(f"**Replay Of:** {report.get('replay_of')}")
    lines.extend([
        "",
        "## Score",
        "",
        f"**Overall:** {report.get('summary', {}).get('score', 0):.4f}",
        f"**Reasoning:** {report.get('summary', {}).get('reasoning', '')}",
        "",
    ])

    dims = report.get("summary", {}).get("dimension_scores", {})
    if dims:
        lines.extend(["### Dimension Scores", "", "| Dimension | Score |", "|-----------|-------|"])
        for dim, val in dims.items():
            lines.append(f"| {dim} | {val:.4f} |")
        lines.append("")

    sweep_results = (report.get("sweep", {}) or {}).get("results", [])
    if sweep_results:
        lines.extend(["## Sweep Results", "", "| Variables | Score | Reasoning |", "|-----------|-------|-----------|"])
        for row in sweep_results:
            lines.append(
                f"| {json.dumps(row.get('variables', {}), sort_keys=True)} "
                f"| {row.get('score', 0):.4f} | {row.get('reasoning', '')} |"
            )
        lines.append("")

    assumptions = report.get("assumptions", [])
    if assumptions:
        lines.extend(["## Assumptions", ""])
        lines.extend(f"- {a}" for a in assumptions)
        lines.append("")

    warnings = report.get("warnings", [])
    if warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- ⚠ {w}" for w in warnings)
        lines.append("")

    path = output_dir / f"{_export_stem(report)}_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "completed", "format": "markdown", "output_path": str(path)}


def _export_csv(report: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    variable_keys = _collect_variable_keys(report)
    dimension_keys = _collect_dimension_keys(report)
    rows = []

    sweep_results = (report.get("sweep", {}) or {}).get("results", [])
    if sweep_results:
        for row in sweep_results:
            row_variables = {**(report.get("variables") or {}), **(row.get("variables") or {})}
            row_dimensions = row.get("dimension_scores", {}) or {}
            rows.append({
                **{key: _stringify_csv_value(row_variables.get(key)) for key in variable_keys},
                "score": _stringify_csv_value(row.get("score")),
                "reasoning": _stringify_csv_value(row.get("reasoning")),
                **{key: _stringify_csv_value(row_dimensions.get(key)) for key in dimension_keys},
            })
    else:
        summary = report.get("summary", {}) or {}
        rows.append({
            **{key: _stringify_csv_value((report.get("variables") or {}).get(key)) for key in variable_keys},
            "score": _stringify_csv_value(summary.get("score")),
            "reasoning": _stringify_csv_value(summary.get("reasoning")),
            **{key: _stringify_csv_value((summary.get("dimension_scores", {}) or {}).get(key)) for key in dimension_keys},
        })

    headers = [*variable_keys, "score", "reasoning", *dimension_keys]
    csv_lines = [",".join(headers)]
    for row in rows:
        csv_lines.append(",".join(_escape_csv(row.get(header, "")) for header in headers))

    path = output_dir / f"{_export_stem(report)}_data.csv"
    path.write_text("\n".join(csv_lines), encoding="utf-8")
    return {"status": "completed", "format": "csv", "output_path": str(path)}


def _escape_csv(value: Any) -> str:
    text = str(value)
    if any(char in text for char in [",", "\"", "\n"]):
        return "\"" + text.replace("\"", "\"\"") + "\""
    return text
