"""tests for the eval_suite resolver that loads a held-out suite from disk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocontext.ambient.eval_suite import EvalCase, EvalSuite, load_eval_suite


def test_absent_file_returns_none(tmp_path: Path) -> None:
    assert load_eval_suite(tmp_path, "competitor_holdout") is None


def test_two_line_suite_parses_prompt_and_reference(tmp_path: Path) -> None:
    (tmp_path / "holdout.jsonl").write_text(
        json.dumps({"prompt": "solve x", "reference": "x=1"}) + "\n" + json.dumps({"prompt": "solve y"}) + "\n",
        encoding="utf-8",
    )
    suite = load_eval_suite(tmp_path, "holdout")
    assert suite == EvalSuite(
        name="holdout",
        cases=[EvalCase(prompt="solve x", reference="x=1"), EvalCase(prompt="solve y", reference="")],
    )


def test_malformed_and_promptless_lines_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "holdout.jsonl").write_text(
        json.dumps({"prompt": "keep me", "reference": "r"})
        + "\n"
        + "this is not json\n"
        + json.dumps({"reference": "no prompt here"})
        + "\n"
        + json.dumps({"prompt": ""})
        + "\n",
        encoding="utf-8",
    )
    suite = load_eval_suite(tmp_path, "holdout")
    assert suite is not None
    assert suite.cases == [EvalCase(prompt="keep me", reference="r")]


def test_empty_file_yields_empty_suite_not_none(tmp_path: Path) -> None:
    (tmp_path / "holdout.jsonl").write_text("\n  \n", encoding="utf-8")
    suite = load_eval_suite(tmp_path, "holdout")
    assert suite is not None
    assert suite.cases == []


@pytest.mark.parametrize("name", ["../secrets", "sub/holdout", ".."])
def test_name_with_path_separator_raises(tmp_path: Path, name: str) -> None:
    outside = tmp_path.parent / "secrets.jsonl"
    outside.write_text(json.dumps({"prompt": "leaked"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_eval_suite(tmp_path, name)
