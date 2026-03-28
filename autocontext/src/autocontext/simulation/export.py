"""Simulation export — portable result packages (AC-453, parity with TS AC-452)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def export_simulation(
    id: str,
    knowledge_root: Path,
    format: str = "json",
) -> dict[str, Any]:
    """Export a saved simulation as a portable package."""
    sim_dir = knowledge_root / "_simulations" / id
    report_path = sim_dir / "report.json"

    if not report_path.exists():
        return {"status": "failed", "error": f"Simulation '{id}' not found", "format": format}

    report = json.loads(report_path.read_text(encoding="utf-8"))
    spec_path = sim_dir / "spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.exists() else {}

    output_dir = sim_dir / "exports"
    output_dir.mkdir(parents=True, exist_ok=True)

    if format == "markdown":
        return _export_markdown(report, spec, output_dir)
    return _export_json(report, spec, output_dir)


def _export_json(report: dict[str, Any], spec: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    pkg = {
        "name": report.get("name", ""),
        "family": report.get("family", "simulation"),
        "description": report.get("description", ""),
        "spec": spec,
        "variables": report.get("variables", {}),
        "results": report.get("summary", {}),
        "sweep": report.get("sweep"),
        "assumptions": report.get("assumptions", []),
        "warnings": report.get("warnings", []),
    }
    path = output_dir / f"{report.get('name', 'sim')}_export.json"
    path.write_text(json.dumps(pkg, indent=2), encoding="utf-8")
    return {"status": "completed", "format": "json", "output_path": str(path)}


def _export_markdown(report: dict[str, Any], spec: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    name = report.get("name", "simulation")
    lines = [
        f"# Simulation Report: {name}",
        "",
        f"**Family:** {report.get('family', 'simulation')}",
        f"**Status:** {report.get('status', 'unknown')}",
        f"**Description:** {report.get('description', '')}",
        "",
        "## Score",
        "",
        f"**Overall:** {report.get('summary', {}).get('score', 0):.4f}",
        f"**Reasoning:** {report.get('summary', {}).get('reasoning', '')}",
        "",
    ]

    dims = report.get("summary", {}).get("dimension_scores", {})
    if dims:
        lines.extend(["### Dimension Scores", "", "| Dimension | Score |", "|-----------|-------|"])
        for dim, val in dims.items():
            lines.append(f"| {dim} | {val:.4f} |")
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

    path = output_dir / f"{name}_report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "completed", "format": "markdown", "output_path": str(path)}
