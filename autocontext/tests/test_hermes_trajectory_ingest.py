"""AC-706 (slice 1): trajectory JSONL ingest with redaction.

Covers:
* normal ShareGPT-like trajectory passes through with redacted text,
* corrupt lines are skipped with a warning (not a hard failure),
* `--limit` caps trajectories written,
* `--dry-run` returns the redaction counts without writing the output,
* the input file is never mutated,
* user-defined patterns flow through the policy,
* `messages[*].content` plus `prompt`/`response`/`output`/`input` are
  redacted; unrelated fields are preserved,
* `FileNotFoundError` surfaces when the input path is missing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from autocontext.hermes.redaction import RedactionPolicy, UserPattern
from autocontext.hermes.trajectory_ingest import (
    TrajectoryIngestSummary,
    ingest_trajectory_jsonl,
)


def _write_jsonl(path: Path, entries: list[dict | str]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            if isinstance(entry, str):
                fh.write(entry + "\n")
            else:
                fh.write(json.dumps(entry) + "\n")


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_normal_trajectory_redacts_message_content(tmp_path: Path) -> None:
    src = tmp_path / "trajectories.jsonl"
    _write_jsonl(
        src,
        [
            {
                "messages": [
                    {"role": "user", "content": "my key is sk-ant-abcdef1234567890abcdef"},
                    {"role": "assistant", "content": "ack"},
                ],
                "trajectory_id": "tj-1",
            }
        ],
    )
    out = tmp_path / "redacted.jsonl"

    summary = ingest_trajectory_jsonl(
        input_path=src,
        output_path=out,
        policy=RedactionPolicy(mode="standard"),
    )

    assert isinstance(summary, TrajectoryIngestSummary)
    assert summary.lines_read == 1
    assert summary.trajectories_written == 1
    rows = _load_jsonl(out)
    assert "sk-ant-" not in rows[0]["messages"][0]["content"]
    assert "[REDACTED_API_KEY]" in rows[0]["messages"][0]["content"]
    # Unrelated fields pass through.
    assert rows[0]["trajectory_id"] == "tj-1"
    # Per-category counts are recorded.
    assert summary.redactions.total >= 1
    assert any(c.startswith("api_key:") for c in summary.redactions.by_category)


def test_corrupt_json_line_is_skipped_with_warning(tmp_path: Path) -> None:
    src = tmp_path / "trajectories.jsonl"
    _write_jsonl(
        src,
        [
            {"messages": [{"role": "user", "content": "ok"}]},
            "{not valid json",  # mid-file corruption
            {"messages": [{"role": "user", "content": "second"}]},
        ],
    )
    out = tmp_path / "redacted.jsonl"

    summary = ingest_trajectory_jsonl(
        input_path=src,
        output_path=out,
        policy=RedactionPolicy(mode="standard"),
    )

    assert summary.lines_read == 3
    assert summary.trajectories_written == 2
    assert summary.skipped == 1
    assert any("malformed JSON" in w for w in summary.warnings)
    rows = _load_jsonl(out)
    assert len(rows) == 2


def test_non_object_line_is_skipped_with_warning(tmp_path: Path) -> None:
    """ShareGPT-like trajectories must be JSON objects; a bare array or
    string should not abort the import but should be skipped with a
    warning."""
    src = tmp_path / "trajectories.jsonl"
    _write_jsonl(
        src,
        [
            {"messages": []},
            "[1,2,3]",
            '"just a string"',
        ],
    )
    out = tmp_path / "redacted.jsonl"
    summary = ingest_trajectory_jsonl(
        input_path=src,
        output_path=out,
        policy=RedactionPolicy(mode="standard"),
    )
    assert summary.trajectories_written == 1
    assert summary.skipped == 2


def test_limit_caps_trajectories_written(tmp_path: Path) -> None:
    src = tmp_path / "trajectories.jsonl"
    _write_jsonl(
        src,
        [{"messages": [{"role": "user", "content": f"line {i}"}]} for i in range(10)],
    )
    out = tmp_path / "redacted.jsonl"
    summary = ingest_trajectory_jsonl(
        input_path=src,
        output_path=out,
        policy=RedactionPolicy(mode="standard"),
        limit=3,
    )
    assert summary.trajectories_written == 3
    rows = _load_jsonl(out)
    assert len(rows) == 3


def test_dry_run_reports_counts_without_writing_output(tmp_path: Path) -> None:
    src = tmp_path / "trajectories.jsonl"
    _write_jsonl(
        src,
        [{"messages": [{"role": "user", "content": "key sk-ant-abcdef1234567890abcdef"}]}],
    )
    out = tmp_path / "redacted.jsonl"
    summary = ingest_trajectory_jsonl(
        input_path=src,
        output_path=out,
        policy=RedactionPolicy(mode="standard"),
        dry_run=True,
    )
    assert summary.dry_run is True
    assert summary.trajectories_written == 1
    assert summary.redactions.total >= 1
    # AC-706: no write on dry-run.
    assert not out.exists()


def test_input_file_is_never_modified(tmp_path: Path) -> None:
    src = tmp_path / "trajectories.jsonl"
    original = '{"messages": [{"role": "user", "content": "sk-ant-abcdef1234567890abcdef"}]}\n'
    src.write_text(original, encoding="utf-8")
    out = tmp_path / "redacted.jsonl"
    ingest_trajectory_jsonl(
        input_path=src,
        output_path=out,
        policy=RedactionPolicy(mode="standard"),
    )
    # AC-706 requirement: importer never writes to Hermes session/trajectory files.
    assert src.read_text(encoding="utf-8") == original


def test_user_pattern_redacts_in_strict_mode(tmp_path: Path) -> None:
    src = tmp_path / "trajectories.jsonl"
    _write_jsonl(
        src,
        [{"messages": [{"role": "user", "content": "see ticket TKT-12345 in the queue"}]}],
    )
    out = tmp_path / "redacted.jsonl"
    policy = RedactionPolicy(
        mode="strict",
        user_patterns=(UserPattern(name="ticket", pattern=re.compile(r"TKT-\d+")),),
    )
    summary = ingest_trajectory_jsonl(input_path=src, output_path=out, policy=policy)
    rows = _load_jsonl(out)
    assert "TKT-12345" not in rows[0]["messages"][0]["content"]
    assert "[REDACTED_USER_PATTERN:ticket]" in rows[0]["messages"][0]["content"]
    assert summary.redactions.by_category.get("user_pattern:ticket") == 1


def test_prompt_response_output_input_fields_are_redacted(tmp_path: Path) -> None:
    src = tmp_path / "trajectories.jsonl"
    _write_jsonl(
        src,
        [
            {
                "prompt": "send to alice@example.com",
                "response": "ok",
                "output": "wrote to /Users/alice/file",
                "input": "see /Users/alice/.ssh/id_rsa",
                "extra": "kept verbatim",
            }
        ],
    )
    out = tmp_path / "redacted.jsonl"
    ingest_trajectory_jsonl(
        input_path=src,
        output_path=out,
        policy=RedactionPolicy(mode="standard"),
    )
    row = _load_jsonl(out)[0]
    assert "alice@example.com" not in row["prompt"]
    assert "/Users/alice" not in row["output"]
    # `/Users/alice/.ssh/id_rsa` is matched by the absolute-path layer
    # before the high-risk-context layer runs, so it ends up as
    # `[REDACTED_PATH]` rather than the high-risk marker. Either way
    # the sensitive token is gone.
    assert "/Users/alice/.ssh" not in row["input"]
    assert "[REDACTED_PATH]" in row["input"]
    # Unrelated fields are preserved verbatim.
    assert row["extra"] == "kept verbatim"


def test_missing_input_raises_file_not_found(tmp_path: Path) -> None:
    out = tmp_path / "redacted.jsonl"
    with pytest.raises(FileNotFoundError, match="trajectory input not found"):
        ingest_trajectory_jsonl(
            input_path=tmp_path / "does-not-exist.jsonl",
            output_path=out,
            policy=RedactionPolicy(mode="standard"),
        )


def test_empty_input_produces_empty_output(tmp_path: Path) -> None:
    src = tmp_path / "trajectories.jsonl"
    src.write_text("", encoding="utf-8")
    out = tmp_path / "redacted.jsonl"
    summary = ingest_trajectory_jsonl(
        input_path=src,
        output_path=out,
        policy=RedactionPolicy(mode="standard"),
    )
    assert summary.lines_read == 0
    assert summary.trajectories_written == 0
    assert out.exists()
    assert out.read_text(encoding="utf-8") == ""


def test_cli_ingest_trajectories_writes_redacted_jsonl(tmp_path: Path) -> None:
    """End-to-end: `autoctx hermes ingest-trajectories --redact standard`
    redacts the input and reports counts via --json."""

    from typer.testing import CliRunner

    from autocontext.cli import app

    src = tmp_path / "trajectories.jsonl"
    _write_jsonl(
        src,
        [{"messages": [{"role": "user", "content": "key sk-ant-abcdef1234567890abcdef"}]}],
    )
    out = tmp_path / "redacted.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "ingest-trajectories",
            "--input",
            str(src),
            "--output",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["trajectories_written"] == 1
    assert payload["redactions"]["total"] >= 1
    assert out.exists()


def test_cli_ingest_trajectories_rejects_invalid_user_patterns_json(tmp_path: Path) -> None:
    """`--user-patterns 'not-json'` must fail loudly rather than silently
    fall through to standard redaction."""

    from typer.testing import CliRunner

    from autocontext.cli import app

    src = tmp_path / "trajectories.jsonl"
    _write_jsonl(src, [{"messages": [{"role": "user", "content": "ok"}]}])
    out = tmp_path / "redacted.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "ingest-trajectories",
            "--input",
            str(src),
            "--output",
            str(out),
            "--redact",
            "strict",
            "--user-patterns",
            "{not json",
            "--json",
        ],
    )
    assert result.exit_code != 0


def test_blank_lines_are_skipped_silently(tmp_path: Path) -> None:
    """trajectory_samples.jsonl exports occasionally include trailing
    blank lines or blank separators between batches. They should not
    count as `lines_read` or warnings; just skip them."""
    src = tmp_path / "trajectories.jsonl"
    src.write_text(
        '{"messages": [{"role": "user", "content": "a"}]}\n\n{"messages": [{"role": "user", "content": "b"}]}\n\n',
        encoding="utf-8",
    )
    out = tmp_path / "redacted.jsonl"
    summary = ingest_trajectory_jsonl(
        input_path=src,
        output_path=out,
        policy=RedactionPolicy(mode="standard"),
    )
    assert summary.lines_read == 2
    assert summary.skipped == 0
    assert summary.trajectories_written == 2
