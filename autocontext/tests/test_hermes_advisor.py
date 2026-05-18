"""AC-708 slice 1: curator advisor data layer + baseline + metrics.

DDD/TDD coverage for the foundation of the advisor training surface:

* :class:`CuratorDecisionExample` loads cleanly from AC-705 export JSONL,
* malformed / incomplete rows are rejected at the boundary,
* :class:`BaselineAdvisor` always predicts the majority class,
* :func:`evaluate` returns per-label precision/recall plus an
  ``insufficient_data`` flag when the dataset is too small to be
  meaningful (acceptance criteria: "clear 'not enough data' failure
  mode for small Hermes homes"),
* CLI subcommand wires through (`autoctx hermes train-advisor`).

The ML backends (logistic regression, MLX, CUDA) are deferred to
slice 2. This slice establishes the data + evaluation contract so
the backends plug into it without redesigning the surface.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocontext.hermes.advisor import (
    AdvisorMetrics,
    BaselineAdvisor,
    CuratorDecisionExample,
    evaluate,
    load_curator_examples,
    train_baseline,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def _ac705_row(
    *,
    skill_name: str,
    label: str,
    state: str = "active",
    provenance: str = "agent-created",
    pinned: bool = False,
    use_count: int = 0,
    view_count: int = 0,
    patch_count: int = 0,
) -> dict:
    """Build a row in the AC-705 export schema."""
    return {
        "example_id": f"run-001:{skill_name}:{label}",
        "task_kind": "curator-decisions",
        "source": {"curator_run_path": "/tmp/run.json", "started_at": "2026-05-10T00:00:00Z"},
        "input": {
            "skill_name": skill_name,
            "skill_state": state,
            "skill_provenance": provenance,
            "skill_pinned": pinned,
            "skill_use_count": use_count,
            "skill_view_count": view_count,
            "skill_patch_count": patch_count,
            "skill_activity_count": use_count + view_count + patch_count,
            "skill_last_activity_at": None,
        },
        "label": label,
        "confidence": "strong",
        "redactions": [],
        "context": {"run_provider": "anthropic", "run_model": "claude-sonnet-4-5", "run_counts": {}},
    }


# --- load_curator_examples -------------------------------------------------


def test_loads_ac705_export_into_typed_examples(tmp_path: Path) -> None:
    src = tmp_path / "data.jsonl"
    _write_jsonl(
        src,
        [
            _ac705_row(skill_name="s1", label="consolidated", use_count=12),
            _ac705_row(skill_name="s2", label="pruned"),
        ],
    )
    examples = load_curator_examples(src)
    assert len(examples) == 2
    assert isinstance(examples[0], CuratorDecisionExample)
    assert examples[0].skill_name == "s1"
    assert examples[0].label == "consolidated"
    assert examples[0].use_count == 12


def test_load_skips_malformed_json_with_warning(tmp_path: Path) -> None:
    """Per-line tolerance matches the AC-704/706 ingest posture: one
    bad line should not abort the whole load."""
    src = tmp_path / "data.jsonl"
    src.write_text(
        json.dumps(_ac705_row(skill_name="s1", label="consolidated"))
        + "\n"
        + "{not valid json\n"
        + json.dumps(_ac705_row(skill_name="s2", label="pruned"))
        + "\n",
        encoding="utf-8",
    )
    examples = load_curator_examples(src)
    assert len(examples) == 2
    assert {ex.skill_name for ex in examples} == {"s1", "s2"}


def test_load_rejects_row_missing_label(tmp_path: Path) -> None:
    src = tmp_path / "data.jsonl"
    row = _ac705_row(skill_name="s1", label="consolidated")
    row.pop("label")
    _write_jsonl(src, [row])
    examples = load_curator_examples(src)
    # Skipped, not raised — same posture as malformed JSON.
    assert examples == []


def test_load_rejects_unknown_label(tmp_path: Path) -> None:
    src = tmp_path / "data.jsonl"
    row = _ac705_row(skill_name="s1", label="invented-label")
    _write_jsonl(src, [row])
    examples = load_curator_examples(src)
    assert examples == []


def test_load_skips_row_with_non_numeric_int_field(tmp_path: Path) -> None:
    """PR #972 review (P2): a row with a non-numeric `skill_use_count`
    must not abort the loader. The contract is per-line tolerant
    (matches AC-704 / AC-706 ingest posture)."""
    src = tmp_path / "data.jsonl"
    bad = _ac705_row(skill_name="s_bad", label="consolidated")
    bad["input"]["skill_use_count"] = "not-an-int"
    good = _ac705_row(skill_name="s_good", label="pruned")
    _write_jsonl(src, [bad, good])
    examples = load_curator_examples(src)
    assert [ex.skill_name for ex in examples] == ["s_good"]


def test_load_skips_row_with_negative_numeric_string(tmp_path: Path) -> None:
    """Numeric strings (`"12"`) coerce cleanly; non-numeric strings
    skip the row. The negative-int case is allowed (Hermes can record
    rollback counts) so it does NOT skip."""
    src = tmp_path / "data.jsonl"
    row = _ac705_row(skill_name="s1", label="consolidated")
    row["input"]["skill_view_count"] = "-3"
    _write_jsonl(src, [row])
    examples = load_curator_examples(src)
    assert len(examples) == 1
    assert examples[0].view_count == -3


def test_load_empty_file_returns_empty_list(tmp_path: Path) -> None:
    src = tmp_path / "data.jsonl"
    src.write_text("", encoding="utf-8")
    assert load_curator_examples(src) == []


# --- BaselineAdvisor + train_baseline -------------------------------------


def test_baseline_predicts_majority_class() -> None:
    examples = [
        CuratorDecisionExample(
            skill_name="s1",
            label="consolidated",
            state="active",
            provenance="agent-created",
            pinned=False,
            use_count=0,
            view_count=0,
            patch_count=0,
        ),
        CuratorDecisionExample(
            skill_name="s2",
            label="consolidated",
            state="active",
            provenance="agent-created",
            pinned=False,
            use_count=0,
            view_count=0,
            patch_count=0,
        ),
        CuratorDecisionExample(
            skill_name="s3",
            label="pruned",
            state="active",
            provenance="agent-created",
            pinned=False,
            use_count=0,
            view_count=0,
            patch_count=0,
        ),
    ]
    advisor = train_baseline(examples)
    assert isinstance(advisor, BaselineAdvisor)
    assert advisor.predict(examples[0]) == "consolidated"
    assert advisor.predict(examples[2]) == "consolidated"  # still predicts majority


def test_baseline_breaks_ties_deterministically() -> None:
    """Equal counts → pick the first label seen, in alphabetical order
    of the canonical label set so two runs over the same data agree."""
    examples = [
        CuratorDecisionExample(
            skill_name=f"s{i}",
            label=lbl,
            state="active",
            provenance="agent-created",
            pinned=False,
            use_count=0,
            view_count=0,
            patch_count=0,
        )
        for i, lbl in enumerate(["pruned", "consolidated"])
    ]
    advisor1 = train_baseline(examples)
    advisor2 = train_baseline(list(reversed(examples)))
    assert advisor1.predict(examples[0]) == advisor2.predict(examples[0])


def test_baseline_on_empty_dataset_raises_clear_error() -> None:
    with pytest.raises(ValueError, match="no labeled examples"):
        train_baseline([])


# --- evaluate + AdvisorMetrics --------------------------------------------


def test_evaluate_reports_per_label_precision_recall() -> None:
    # 3 consolidated + 1 pruned; baseline predicts "consolidated" for all 4.
    examples = [
        CuratorDecisionExample(
            skill_name="s1",
            label="consolidated",
            state="active",
            provenance="agent-created",
            pinned=False,
            use_count=0,
            view_count=0,
            patch_count=0,
        ),
        CuratorDecisionExample(
            skill_name="s2",
            label="consolidated",
            state="active",
            provenance="agent-created",
            pinned=False,
            use_count=0,
            view_count=0,
            patch_count=0,
        ),
        CuratorDecisionExample(
            skill_name="s3",
            label="consolidated",
            state="active",
            provenance="agent-created",
            pinned=False,
            use_count=0,
            view_count=0,
            patch_count=0,
        ),
        CuratorDecisionExample(
            skill_name="s4",
            label="pruned",
            state="active",
            provenance="agent-created",
            pinned=False,
            use_count=0,
            view_count=0,
            patch_count=0,
        ),
    ]
    advisor = train_baseline(examples)
    metrics = evaluate(advisor, examples)

    assert isinstance(metrics, AdvisorMetrics)
    # Baseline always predicts "consolidated".
    # Consolidated: TP=3, FP=1, FN=0 → precision 3/4 = 0.75, recall 3/3 = 1.0
    assert metrics.per_label["consolidated"].precision == pytest.approx(0.75)
    assert metrics.per_label["consolidated"].recall == pytest.approx(1.0)
    # Pruned: TP=0, FP=0, FN=1 → precision 0 (no positives predicted), recall 0
    assert metrics.per_label["pruned"].precision == pytest.approx(0.0)
    assert metrics.per_label["pruned"].recall == pytest.approx(0.0)
    # Overall accuracy is 3/4.
    assert metrics.accuracy == pytest.approx(0.75)


def test_evaluate_flags_insufficient_data() -> None:
    """AC-708 acceptance: 'a clear not enough data failure mode for
    small Hermes homes'. Threshold of 20 examples is a reasonable
    floor for any per-label precision/recall to be meaningful."""
    examples = [
        CuratorDecisionExample(
            skill_name=f"s{i}",
            label="consolidated",
            state="active",
            provenance="agent-created",
            pinned=False,
            use_count=0,
            view_count=0,
            patch_count=0,
        )
        for i in range(5)
    ]
    advisor = train_baseline(examples)
    metrics = evaluate(advisor, examples)
    assert metrics.insufficient_data is True
    # But the per-label numbers still come back; the consumer just
    # knows not to trust them yet.
    assert metrics.per_label["consolidated"].precision == pytest.approx(1.0)


def test_evaluate_clears_insufficient_data_flag_with_enough_examples() -> None:
    examples = [
        CuratorDecisionExample(
            skill_name=f"s{i}",
            label="consolidated",
            state="active",
            provenance="agent-created",
            pinned=False,
            use_count=0,
            view_count=0,
            patch_count=0,
        )
        for i in range(25)
    ]
    advisor = train_baseline(examples)
    metrics = evaluate(advisor, examples)
    assert metrics.insufficient_data is False


def test_metrics_serialize_to_json_friendly_dict() -> None:
    examples = [
        CuratorDecisionExample(
            skill_name="s1",
            label="consolidated",
            state="active",
            provenance="agent-created",
            pinned=False,
            use_count=0,
            view_count=0,
            patch_count=0,
        ),
        CuratorDecisionExample(
            skill_name="s2",
            label="pruned",
            state="active",
            provenance="agent-created",
            pinned=False,
            use_count=0,
            view_count=0,
            patch_count=0,
        ),
    ]
    advisor = train_baseline(examples)
    metrics = evaluate(advisor, examples)
    payload = metrics.to_dict()
    assert "per_label" in payload
    assert "accuracy" in payload
    assert "insufficient_data" in payload
    assert "example_count" in payload
    json.dumps(payload)  # must round-trip through JSON


# --- CLI integration ------------------------------------------------------


def test_cli_train_advisor_writes_metrics_json(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from autocontext.cli import app

    src = tmp_path / "data.jsonl"
    _write_jsonl(
        src,
        [_ac705_row(skill_name=f"s{i}", label="consolidated") for i in range(30)]
        + [_ac705_row(skill_name=f"p{i}", label="pruned") for i in range(5)],
    )
    out = tmp_path / "metrics.json"
    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "train-advisor",
            "--data",
            str(src),
            "--baseline",
            "--output",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["advisor_kind"] == "baseline"
    assert payload["metrics"]["accuracy"] >= 0.85  # baseline majority-class on 30/35 = 0.857
    assert out.exists()
    on_disk = json.loads(out.read_text(encoding="utf-8"))
    assert on_disk["metrics"]["accuracy"] == payload["metrics"]["accuracy"]


def test_cli_train_advisor_surfaces_insufficient_data_warning(tmp_path: Path) -> None:
    """When the dataset is too small, the CLI summary must say so so
    the operator does not act on noise."""
    from typer.testing import CliRunner

    from autocontext.cli import app

    src = tmp_path / "data.jsonl"
    _write_jsonl(src, [_ac705_row(skill_name="s1", label="consolidated")])
    out = tmp_path / "metrics.json"
    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "train-advisor",
            "--data",
            str(src),
            "--baseline",
            "--output",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["metrics"]["insufficient_data"] is True


def test_cli_rejects_same_path_for_data_and_output(tmp_path: Path) -> None:
    """PR #972 review (P2): `--output` must not be allowed to equal
    `--data`, otherwise the source dataset gets overwritten with
    metrics JSON."""
    from typer.testing import CliRunner

    from autocontext.cli import app

    src = tmp_path / "data.jsonl"
    _write_jsonl(src, [_ac705_row(skill_name="s1", label="consolidated")])
    original = src.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "train-advisor",
            "--data",
            str(src),
            "--baseline",
            "--output",
            str(src),
            "--json",
        ],
    )
    assert result.exit_code != 0
    # Source dataset is untouched.
    assert src.read_text(encoding="utf-8") == original


def test_cli_rejects_same_path_via_symlink(tmp_path: Path) -> None:
    """A symlink that resolves to the source dataset must also be
    rejected, matching the trajectory-ingest same-file guard."""
    from typer.testing import CliRunner

    from autocontext.cli import app

    src = tmp_path / "data.jsonl"
    _write_jsonl(src, [_ac705_row(skill_name="s1", label="consolidated")])
    link = tmp_path / "link.jsonl"
    link.symlink_to(src)
    original = src.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "train-advisor",
            "--data",
            str(src),
            "--baseline",
            "--output",
            str(link),
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert src.read_text(encoding="utf-8") == original


def test_cli_train_advisor_rejects_empty_dataset(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from autocontext.cli import app

    src = tmp_path / "data.jsonl"
    src.write_text("", encoding="utf-8")
    out = tmp_path / "metrics.json"
    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "train-advisor",
            "--data",
            str(src),
            "--baseline",
            "--output",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code != 0
