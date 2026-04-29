"""Pi-compatible package export tests."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

runner = CliRunner()


def _strategy_package() -> object:
    from autocontext.knowledge.package import StrategyPackage

    return StrategyPackage(
        scenario_name="grid_ctf",
        display_name="Grid CTF",
        description="Capture the flag on a grid.",
        playbook="## Playbook\n\nScout, then strike.",
        lessons=["Prefer short routes.", "Avoid stale scouts."],
        best_strategy={"aggression": 0.7},
        best_score=0.88,
        best_elo=1710.0,
        hints="Watch borders.",
    )


def _setup_db_and_artifacts(tmp_path: Path) -> tuple[SQLiteStore, ArtifactStore, Path]:
    db_path = tmp_path / "runs" / "autocontext.sqlite3"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = SQLiteStore(db_path)
    db.migrate(Path(__file__).resolve().parents[1] / "migrations")
    artifacts = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )
    return db, artifacts, db_path


def _seed_grid_ctf(db: SQLiteStore, artifacts: ArtifactStore) -> None:
    artifacts.write_playbook("grid_ctf", "## Playbook\n\nScout, then strike.")
    artifacts.write_hints("grid_ctf", "Watch borders.")
    db.create_run("pi_pkg_run", "grid_ctf", 1, "local")
    db.mark_run_completed("pi_pkg_run")


def test_pi_package_builds_installable_file_map() -> None:
    from autocontext.knowledge.pi_package import build_pi_package

    package = build_pi_package(_strategy_package())

    assert package.package_dir_name == "grid-ctf-pi-package"
    assert set(package.files) == {
        "README.md",
        "autocontext.package.json",
        "package.json",
        "prompts/grid-ctf.md",
        "skills/grid-ctf-knowledge/SKILL.md",
    }

    manifest = json.loads(package.files["package.json"])
    assert manifest["name"] == "autocontext-grid-ctf-pi-package"
    assert manifest["private"] is True
    assert manifest["pi"]["skills"] == ["skills/grid-ctf-knowledge/SKILL.md"]
    assert manifest["pi"]["prompts"] == ["prompts/grid-ctf.md"]
    assert manifest["autocontext"]["scenario_name"] == "grid_ctf"

    prompt = package.files["prompts/grid-ctf.md"]
    assert "Grid CTF" in prompt
    assert "Scout, then strike." in prompt
    assert "autocontext_export_package" in prompt


def test_pi_package_writer_creates_directory_layout(tmp_path: Path) -> None:
    from autocontext.knowledge.pi_package import build_pi_package, write_pi_package

    target = tmp_path / "pkg"
    written = write_pi_package(build_pi_package(_strategy_package()), target)

    assert written.output_dir == target
    assert sorted(path.relative_to(target).as_posix() for path in written.files) == [
        "README.md",
        "autocontext.package.json",
        "package.json",
        "prompts/grid-ctf.md",
        "skills/grid-ctf-knowledge/SKILL.md",
    ]
    assert (target / "skills" / "grid-ctf-knowledge" / "SKILL.md").read_text(encoding="utf-8").startswith("---")


def test_export_command_writes_pi_package(tmp_path: Path) -> None:
    db, artifacts, db_path = _setup_db_and_artifacts(tmp_path)
    _seed_grid_ctf(db, artifacts)
    output_dir = tmp_path / "grid-ctf-pi-package"

    result = runner.invoke(app, [
        "export",
        "--format", "pi-package",
        "--scenario", "grid_ctf",
        "--output", str(output_dir),
        "--db-path", str(db_path),
        "--knowledge-root", str(tmp_path / "knowledge"),
        "--skills-root", str(tmp_path / "skills"),
        "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
    ])

    assert result.exit_code == 0, result.output
    manifest = json.loads((output_dir / "package.json").read_text(encoding="utf-8"))
    assert manifest["pi"]["skills"] == ["skills/grid-ctf-knowledge/SKILL.md"]
    assert (output_dir / "autocontext.package.json").exists()


def test_export_command_reports_pi_package_json(tmp_path: Path) -> None:
    db, artifacts, db_path = _setup_db_and_artifacts(tmp_path)
    _seed_grid_ctf(db, artifacts)
    output_dir = tmp_path / "grid-ctf-pi-package"

    result = runner.invoke(app, [
        "export",
        "--json",
        "--format", "pi-package",
        "--scenario", "grid_ctf",
        "--output", str(output_dir),
        "--db-path", str(db_path),
        "--knowledge-root", str(tmp_path / "knowledge"),
        "--skills-root", str(tmp_path / "skills"),
        "--claude-skills-path", str(tmp_path / ".claude" / "skills"),
    ])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["format"] == "pi-package"
    assert payload["output_path"] == str(output_dir)
    assert payload["file_count"] == 5


def test_export_help_describes_format_dependent_output_path() -> None:
    result = runner.invoke(app, ["export", "--help"])

    assert result.exit_code == 0, result.output
    assert "Output path: strategy JSON file" in result.output
    assert "pi-package directory" in result.output
    assert "Output JSON file path" not in result.output
