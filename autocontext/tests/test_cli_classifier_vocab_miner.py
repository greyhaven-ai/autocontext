"""AC-582 — CLI tests for `autoctx classifier-mine-vocab`."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.scenarios.custom.classifier_cache import _schema_version
from autocontext.scenarios.families import list_families

runner = CliRunner()


def _write_cache(path: Path, entries: dict) -> None:
    """Write a minimal cache.json at *path*."""
    schema = _schema_version([family.name for family in list_families()])
    raw_entries = {}
    for family, descs in entries.items():
        for desc in descs:
            key = hashlib.sha256(desc.encode()).hexdigest()
            raw_entries[key] = {
                "family_name": family,
                "confidence": 0.8,
                "rationale": "r",
                "no_signals_matched": False,
                "description": desc,
                "cached_at": "2026-04-22T00:00:00+00:00",
            }
    path.write_text(
        json.dumps({"schema_version": schema, "entries": raw_entries}),
        encoding="utf-8",
    )


class TestClassifierMineVocabCommand:
    def test_command_is_registered(self) -> None:
        result = runner.invoke(app, ["classifier-mine-vocab", "--help"])
        assert result.exit_code == 0

    def test_missing_cache_exits_with_message(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["classifier-mine-vocab", "--cache", str(tmp_path / "missing.json")],
        )
        assert result.exit_code == 0
        assert "0" in result.output  # 0 proposals or 0 entries

    def test_produces_markdown_report_to_stdout(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        _write_cache(
            cache_path,
            {
                "agent_task": [
                    "biomedical drug study",
                    "biomedical research protocol",
                    "biomedical literature summary",
                ]
            },
        )
        result = runner.invoke(
            app,
            ["classifier-mine-vocab", "--cache", str(cache_path), "--min-occurrences", "3"],
        )
        assert result.exit_code == 0
        assert "biomedical" in result.output

    def test_out_flag_writes_file_instead_of_stdout(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        out_path = tmp_path / "proposals.md"
        _write_cache(
            cache_path,
            {
                "agent_task": [
                    "biomedical drug study",
                    "biomedical research protocol",
                    "biomedical literature summary",
                ]
            },
        )
        result = runner.invoke(
            app,
            [
                "classifier-mine-vocab",
                "--cache",
                str(cache_path),
                "--out",
                str(out_path),
                "--min-occurrences",
                "3",
            ],
        )
        assert result.exit_code == 0
        assert out_path.exists()
        content = out_path.read_text(encoding="utf-8")
        assert "biomedical" in content

    def test_default_cache_path_used_when_flag_omitted(self, tmp_path: Path) -> None:
        """When --cache is omitted the command uses the default path (may be empty)."""
        default_cache = tmp_path / "classifier_fallback.json"
        _write_cache(
            default_cache,
            {
                "simulation": [
                    "logistics routing system",
                    "logistics warehouse design",
                    "logistics freight analysis",
                ]
            },
        )
        with patch(
            "autocontext.scenarios.custom.classifier_vocab_miner._default_cache_path",
            return_value=default_cache,
        ):
            result = runner.invoke(app, ["classifier-mine-vocab", "--min-occurrences", "3"])
        assert result.exit_code == 0
        assert "logistics" in result.output

    def test_min_occurrences_flag_controls_threshold(self, tmp_path: Path) -> None:
        cache_path = tmp_path / "cache.json"
        _write_cache(
            cache_path,
            {
                "agent_task": [
                    "cryptographic protocol analysis",
                    "cryptographic key derivation",
                ]
            },
        )
        # threshold 3 → no proposal
        result3 = runner.invoke(
            app,
            ["classifier-mine-vocab", "--cache", str(cache_path), "--min-occurrences", "3"],
        )
        assert result3.exit_code == 0
        assert "cryptographic" not in result3.output

        # threshold 2 → proposal present
        result2 = runner.invoke(
            app,
            ["classifier-mine-vocab", "--cache", str(cache_path), "--min-occurrences", "2"],
        )
        assert result2.exit_code == 0
        assert "cryptographic" in result2.output
