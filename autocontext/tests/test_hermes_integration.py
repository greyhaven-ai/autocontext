from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.hermes.inspection import inspect_hermes_home
from autocontext.hermes.skill import render_autocontext_skill

runner = CliRunner()


def _write_skill(root: Path, relative_dir: str, *, name: str, description: str = "Use when testing.") -> Path:
    skill_dir = root / "skills" / relative_dir
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(
        "\n".join([
            "---",
            f"name: {name}",
            f"description: {description}",
            "version: 1.0.0",
            "author: Test",
            "license: MIT",
            "---",
            "",
            f"# {name}",
            "",
            "Body.",
        ]),
        encoding="utf-8",
    )
    return path


def _seed_hermes_home(tmp_path: Path) -> Path:
    home = tmp_path / ".hermes"
    skills_root = home / "skills"
    _write_skill(home, "software-development/autocontext", name="autocontext")
    _write_skill(home, "software-development/bundled-helper", name="bundled-helper")
    _write_skill(home, "data-science/hub-helper", name="hub-helper")
    _write_skill(home, ".archive/old-skill", name="old-skill")

    (skills_root / ".bundled_manifest").write_text("bundled-helper:sha256-demo\n", encoding="utf-8")
    (skills_root / ".hub").mkdir(parents=True, exist_ok=True)
    (skills_root / ".hub" / "lock.json").write_text(
        json.dumps({"installed": {"hub-helper": {"version": "1.2.3"}}}),
        encoding="utf-8",
    )
    (skills_root / ".usage.json").write_text(
        json.dumps(
            {
                "autocontext": {
                    "use_count": 5,
                    "view_count": 2,
                    "patch_count": 1,
                    "last_used_at": "2026-04-30T18:00:00+00:00",
                    "last_viewed_at": "2026-04-30T18:05:00+00:00",
                    "last_patched_at": "2026-04-30T17:00:00+00:00",
                    "created_at": "2026-04-30T16:00:00+00:00",
                    "state": "active",
                    "pinned": True,
                    "archived_at": None,
                },
                "bundled-helper": {"use_count": 9, "state": "active", "pinned": False},
            }
        ),
        encoding="utf-8",
    )

    run_dir = home / "logs" / "curator" / "20260430-183000"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "started_at": "2026-04-30T18:30:00+00:00",
                "duration_seconds": 12.5,
                "model": "qwen/qwen3-30b-a3b",
                "provider": "openai-compatible",
                "counts": {
                    "skills_before": 4,
                    "skills_after": 3,
                    "archived_this_run": 1,
                    "consolidated_this_run": 1,
                    "pruned_this_run": 0,
                },
                "auto_transitions": {"checked": 3, "marked_stale": 1, "archived": 0, "reactivated": 0},
                "tool_call_counts": {"skill_manage": 2},
                "consolidated": [{"name": "old-specific", "into": "umbrella", "reason": "merged"}],
                "pruned": [],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "REPORT.md").write_text("# Curator Report\n", encoding="utf-8")
    return home


def test_inspect_hermes_home_reads_v012_skill_usage_and_curator_reports(tmp_path: Path) -> None:
    home = _seed_hermes_home(tmp_path)

    inventory = inspect_hermes_home(home)

    assert inventory.hermes_home == home
    assert inventory.skill_count == 3
    assert inventory.agent_created_skill_count == 1
    assert inventory.bundled_skill_count == 1
    assert inventory.hub_skill_count == 1
    assert inventory.pinned_skill_count == 1
    assert inventory.archived_skill_count == 1

    autocontext_skill = inventory.skills_by_name["autocontext"]
    assert autocontext_skill.agent_created is True
    assert autocontext_skill.pinned is True
    assert autocontext_skill.activity_count == 8
    assert autocontext_skill.last_activity_at == "2026-04-30T18:05:00+00:00"

    assert inventory.skills_by_name["bundled-helper"].agent_created is False
    assert inventory.skills_by_name["hub-helper"].provenance == "hub"
    assert inventory.curator.run_count == 1
    assert inventory.curator.latest is not None
    assert inventory.curator.latest.counts["consolidated_this_run"] == 1
    assert inventory.curator.latest.report_path == home / "logs" / "curator" / "20260430-183000" / "REPORT.md"


def test_hermes_inspect_cli_outputs_machine_readable_json(tmp_path: Path) -> None:
    home = _seed_hermes_home(tmp_path)

    result = runner.invoke(app, ["hermes", "inspect", "--home", str(home), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["hermes_home"] == str(home)
    assert payload["skill_count"] == 3
    assert payload["agent_created_skill_count"] == 1
    assert payload["curator"]["run_count"] == 1
    assert payload["skills"][0]["name"] == "autocontext"


def test_autocontext_hermes_skill_matches_hermes_frontmatter_contract() -> None:
    skill = render_autocontext_skill()

    assert skill.startswith("---\n")
    frontmatter_text = skill.split("\n---\n", 1)[0].removeprefix("---\n")
    frontmatter = yaml.safe_load(frontmatter_text)
    assert frontmatter["name"] == "autocontext"
    assert frontmatter["description"].startswith("Use when")
    assert len(frontmatter["description"]) <= 1024
    assert frontmatter["metadata"]["hermes"]["tags"]
    assert "# Autocontext" in skill
    assert "autoctx hermes inspect --json" in skill
    assert "MCP is optional" in skill
    assert "Hermes Curator owns Hermes skill mutation" in skill
    assert "MCP primary" not in skill


def test_hermes_export_skill_writes_skill_markdown(tmp_path: Path) -> None:
    output_path = tmp_path / "skills" / "autocontext" / "SKILL.md"

    result = runner.invoke(app, ["hermes", "export-skill", "--output", str(output_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["skill_name"] == "autocontext"
    assert payload["output_path"] == str(output_path)
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8") == render_autocontext_skill().rstrip() + "\n"
