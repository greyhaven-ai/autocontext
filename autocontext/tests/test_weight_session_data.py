from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.comparison_models import ComparisonCase
from autocontext.weight_evolution.datasets import DatasetInstruction, EvaluationProblem
from autocontext.weight_evolution.session_data import MAX_SESSION_DATA_BYTES, SessionDataBundle, load_session_data


def session_data() -> SessionDataBundle:
    return SessionDataBundle(
        practice=(
            ComparisonCase(dataset="math500", problem=EvaluationProblem(
                problem_id="practice/add", text="Add 1 and 1.", answer="practice-gold-two")),
            ComparisonCase(dataset="ifeval", problem=EvaluationProblem(
                problem_id="practice/word", text="Say a greeting.", instructions=(DatasetInstruction(
                    instruction_id="keywords:existence", kwargs={"keywords": ["practice-hidden-keyword"]}),))),
        ),
        confirmation=(ComparisonCase(dataset="gsm8k", problem=EvaluationProblem(
            problem_id="confirmation/sub", text="Subtract 2 from 5.", answer="confirmation-gold-three")),),
        final_audit=(ComparisonCase(dataset="math500", problem=EvaluationProblem(
            problem_id="audit/multiply", text="Multiply 3 by 4.", answer="audit-gold-twelve")),),
    )


def test_practice_projection_exposes_only_selected_questions_in_requested_order() -> None:
    data = session_data()
    projected = data.project_practice(("practice/word", "practice/add"))
    assert [case.problem_id for case in projected] == ["practice/word", "practice/add"]
    assert all(set(case.to_dict()) == {"dataset", "problem_id", "text"} for case in projected)
    text = json.dumps([case.to_dict() for case in projected])
    for hidden in ("gold", "hidden-keyword", "instructions", "answer", "confirmation", "audit", "Subtract", "Multiply"):
        assert hidden not in text
    assert data.practice_cases == data.practice


@pytest.mark.parametrize("ids", [(), ("practice/add", "practice/add"), ("confirmation/sub",), ("audit/multiply",),
                                  ("unknown",), ("practice/add", "audit/multiply")])
def test_projection_refuses_hidden_unknown_and_duplicate_ids(ids: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        session_data().practice_projection(ids)


@pytest.mark.parametrize("source,target", [
    ("practice", "confirmation"), ("practice", "final_audit"), ("confirmation", "final_audit"),
    ("practice", "practice"), ("confirmation", "confirmation"), ("final_audit", "final_audit"),
])
@pytest.mark.parametrize("identity", ["same_id", "normalized_text"])
def test_all_three_roles_reject_cross_role_and_within_role_aliases(source: str, target: str, identity: str) -> None:
    raw = session_data().to_dict()
    case = json.loads(json.dumps(raw[source][0]))
    if identity == "normalized_text":
        case["problem"]["problem_id"] = "new-alias-id"
        case["problem"]["text"] = " \t" + case["problem"]["text"].upper().replace(" ", "   ") + "\n"
    else:
        case["problem"]["text"] = "Different prompt for repeated identity."
    raw[target].append(case)
    with pytest.raises(ValueError, match="identity|alias"):
        SessionDataBundle.model_validate_json(json.dumps(raw))


def test_unicode_compatibility_alias_does_not_bypass_separation() -> None:
    raw = session_data().to_dict()
    raw["confirmation"][0]["problem"]["text"] = "  ＡＤＤ １  AND   １. "
    with pytest.raises(ValueError, match="alias"):
        SessionDataBundle.model_validate_json(json.dumps(raw))


def test_split_commitments_bind_gold_and_hidden_rows_without_exposing_them() -> None:
    data = session_data()
    commitments = data.split_commitments()
    assert set(commitments) == {"practice", "confirmation", "final_audit"}
    assert commitments["practice"]["count"] == 2
    assert all(set(value) == {"digest", "count"} for value in commitments.values())
    raw = data.to_dict()
    raw["final_audit"][0]["problem"]["answer"] = "changed-gold"
    changed = SessionDataBundle.model_validate_json(json.dumps(raw))
    assert changed.digest != data.digest
    assert changed.split_commitments()["final_audit"] != commitments["final_audit"]
    assert changed.split_commitments()["practice"] == commitments["practice"]
    assert "gold" not in json.dumps(commitments) and "Multiply" not in json.dumps(commitments)


def test_loading_requires_both_exact_raw_file_pin_and_valid_role_contract(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    payload = json.dumps(session_data().to_dict()).encode()
    path.write_bytes(payload)
    options = {"expected_sha256": hashlib.sha256(payload).hexdigest(), "expected_size_bytes": len(payload)}
    assert load_session_data(path, **options) == session_data()
    path.write_bytes(payload + b" ")
    with pytest.raises(ValueError, match="size/hash"):
        load_session_data(path, **options)
    path.write_bytes(payload.replace(b"practice-gold-two", b"practice-gold-six"))
    assert path.stat().st_size == len(payload)
    with pytest.raises(ValueError, match="size/hash"):
        load_session_data(path, **options)


@pytest.mark.parametrize("size,digest", [
    (0, "a" * 64), (-1, "a" * 64), (True, "a" * 64), (MAX_SESSION_DATA_BYTES + 1, "a" * 64),
    (10, "A" * 64), (10, "a" * 63), (10, "z" * 64),
])
def test_invalid_data_pins_are_rejected_before_file_access(tmp_path: Path, size: Any, digest: str) -> None:
    with pytest.raises(ValueError, match="pin"):
        load_session_data(tmp_path / "absent", expected_sha256=digest, expected_size_bytes=size)


@pytest.mark.parametrize("mutation", ["duplicate_key", "nonfinite", "missing_role", "live_origin"])
def test_raw_hash_does_not_make_malformed_data_valid(tmp_path: Path, mutation: str) -> None:
    raw = session_data().to_dict()
    if mutation == "missing_role":
        del raw["confirmation"]
    if mutation == "live_origin":
        raw["origin"] = "published_benchmark"
    payload = json.dumps(raw).encode()
    if mutation == "duplicate_key":
        payload = payload.replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1')
    if mutation == "nonfinite":
        payload = payload.replace(b'"schema_version": 1', b'"schema_version": NaN')
    path = tmp_path / "invalid.json"
    path.write_bytes(payload)
    with pytest.raises(ValueError):
        load_session_data(path, expected_sha256=hashlib.sha256(payload).hexdigest(), expected_size_bytes=len(payload))


@pytest.mark.parametrize("kind", ["leaf_symlink", "parent_symlink", "fifo"])
def test_special_files_and_symlink_paths_are_refused(tmp_path: Path, kind: str) -> None:
    payload = json.dumps(session_data().to_dict()).encode()
    original = tmp_path / "original.json"
    original.write_bytes(payload)
    path = tmp_path / "input.json"
    if kind == "leaf_symlink":
        path.symlink_to(original)
    elif kind == "parent_symlink":
        parent = tmp_path / "alias"
        parent.symlink_to(tmp_path, target_is_directory=True)
        path = parent / original.name
    else:
        os.mkfifo(path)
    with pytest.raises(ValueError, match="symlink|regular file"):
        load_session_data(path, expected_sha256=hashlib.sha256(payload).hexdigest(), expected_size_bytes=len(payload))
