from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from autocontext.weight_evolution import datasets
from autocontext.weight_evolution.datasets import (
    DatasetLock,
    DatasetPin,
    EvaluationProblem,
    cohort_digest,
    fetch_datasets,
    load_dataset_lock,
    normalize_dataset,
    overlapping_problem_ids,
    verify_dataset,
    verify_datasets,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/weight_evolution/qwen_reproduction"
ROWS: dict[str, list[dict[str, Any]]] = {
    "math500": [{"unique_id": "synthetic/math-1", "problem": "What is 2+3?", "answer": "5"}],
    "gsm8k": [{"question": "What is 500 + 500?", "answer": "Add the numbers. #### 1,000"}],
    "ifeval": [{"key": 1000, "prompt": "Reply with exactly one word.",
                "instruction_id_list": ["length_constraints:number_words"],
                "kwargs": [{"num_words": 2, "relation": "less than"}]}],
}


def encoded(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(json.dumps(row).encode() + b"\n" for row in rows)


def fixture_lock() -> DatasetLock:
    published = load_dataset_lock(EXAMPLE / "datasets.lock.json")
    pins = []
    for original in published.datasets:
        data = encoded(ROWS[original.dataset])
        values = original.to_dict()
        values.update(sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data), expected_count=1,
                      normalized_sha256=cohort_digest(normalize_dataset(original.dataset, data)))
        pins.append(DatasetPin.model_validate(values))
    return DatasetLock(datasets=tuple(pins))


@pytest.fixture
def local_data(tmp_path: Path) -> tuple[DatasetLock, Path]:
    lock = fixture_lock()
    for pin in lock.datasets:
        (tmp_path / pin.relative_path).write_bytes(encoded(ROWS[pin.dataset]))
    return lock, tmp_path


def test_published_pins_have_actual_counts_and_immutable_sources() -> None:
    lock = load_dataset_lock(EXAMPLE / "datasets.lock.json")
    assert {pin.dataset: pin.expected_count for pin in lock.datasets} == {"math500": 500, "gsm8k": 1319, "ifeval": 541}
    assert sum(pin.size_bytes for pin in lock.datasets) == 1403413
    assert all(pin.revision in pin.url and pin.role == "final_test" for pin in lock.datasets)
    assert len(lock.digest) == 64


def test_normalization_preserves_source_order_ids_and_scoring_fields() -> None:
    math = normalize_dataset("math500", encoded(ROWS["math500"]))[0]
    gsm = normalize_dataset("gsm8k", encoded(ROWS["gsm8k"]))[0]
    ifeval = normalize_dataset("ifeval", encoded(ROWS["ifeval"]))[0]
    assert (math.problem_id, math.answer, math.instructions) == ("synthetic/math-1", "5", ())
    assert (gsm.problem_id, gsm.answer) == ("gsm8k_test_0", "1000")
    assert (ifeval.problem_id, ifeval.answer) == ("ifeval_1000", None)
    assert ifeval.instructions[0].kwargs == {"num_words": 2, "relation": "less than"}
    second = {**ROWS["math500"][0], "unique_id": "synthetic/math-2"}
    cohort = normalize_dataset("math500", encoded([second, ROWS["math500"][0]]))
    assert [problem.problem_id for problem in cohort] == ["synthetic/math-2", "synthetic/math-1"]


def test_cohort_digest_binds_order_answers_and_instruction_arguments() -> None:
    one = EvaluationProblem(problem_id="one", text="A", answer="1")
    two = EvaluationProblem(problem_id="two", text="B", answer="2")
    assert cohort_digest((one, two)) != cohort_digest((two, one))
    assert cohort_digest((one,)) != cohort_digest((one.model_copy(update={"answer": "2"}),))
    original = normalize_dataset("ifeval", encoded(ROWS["ifeval"]))
    altered = json.loads(json.dumps(ROWS["ifeval"]))
    altered[0]["kwargs"][0]["num_words"] = 10
    assert cohort_digest(original) != cohort_digest(normalize_dataset("ifeval", encoded(altered)))


def test_verification_is_offline_and_summaries_omit_protected_payload(
    local_data: tuple[DatasetLock, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(datasets.urllib.request, "build_opener", Mock(side_effect=AssertionError("network")))
    lock, root = local_data
    verified = verify_datasets(lock, root)
    assert len(verified) == 3
    for item in verified:
        assert item.lock_digest == lock.digest
        assert item.digest == cohort_digest(item.problems)
        summary = json.dumps(item.to_dict())
        assert "answer" not in summary and "instructions" not in summary and item.problems[0].text not in summary


@pytest.mark.parametrize("mutation", ["bytes", "count", "digest"])
def test_verification_rejects_source_or_normalized_drift(local_data: tuple[DatasetLock, Path], mutation: str) -> None:
    lock, root = local_data
    if mutation == "bytes":
        path = root / "math500.jsonl"
        path.write_bytes(path.read_bytes().replace(b'"5"', b'"6"'))
    else:
        pin = lock.datasets[0]
        update = {"expected_count": 2} if mutation == "count" else {"normalized_sha256": "f" * 64}
        lock = lock.model_copy(update={"datasets": (pin.model_copy(update=update), *lock.datasets[1:])})
    with pytest.raises(ValueError, match="differs"):
        verify_dataset(lock, root, "math500")


@pytest.mark.parametrize("dataset,row", [
    ("math500", {"unique_id": "a", "problem": "x"}),
    ("math500", {"unique_id": "a", "problem": "", "answer": "1"}),
    ("math500", {"unique_id": " a", "problem": "x", "answer": "1"}),
    ("math500", {"unique_id": "a", "problem": "x", "answer": ""}),
    ("math500", {"unique_id": "a", "problem": "x", "answer": 1}),
    ("gsm8k", {"question": "x", "answer": "No final separator"}),
    ("gsm8k", {"question": "x", "answer": "####1####2"}),
    ("gsm8k", {"question": "x", "answer": "####  "}),
    ("ifeval", {"key": True, "prompt": "x", "instruction_id_list": ["a"], "kwargs": [{}]}),
    ("ifeval", {"key": 1, "prompt": "x", "instruction_id_list": ["a"], "kwargs": []}),
    ("ifeval", {"key": 1, "prompt": "x", "instruction_id_list": [], "kwargs": []}),
    ("ifeval", {"key": 1, "prompt": "x", "instruction_id_list": ["a"], "kwargs": [None]}),
])
def test_malformed_rows_fail_closed(dataset: Any, row: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        normalize_dataset(dataset, encoded([row]))


@pytest.mark.parametrize("payload", [b"", b"\n", b"{}\n\n", b"[]\n", b'{"problem":"x","problem":"y"}\n'])
def test_invalid_jsonl_rejected(payload: bytes) -> None:
    with pytest.raises(ValueError):
        normalize_dataset("math500", payload)


@pytest.mark.parametrize("name", ["math500", "ifeval"])
def test_duplicate_source_problem_ids_rejected(name: Any) -> None:
    with pytest.raises(ValueError, match="duplicate"):
        normalize_dataset(name, encoded(ROWS[name] * 2))


@pytest.mark.parametrize("field,value", [
    ("revision", "main"), ("relative_path", "../escape"), ("relative_path", "other.jsonl"),
    ("url", "https://example.com/file.jsonl"), ("repository", "other/repo"), ("source_path", "other.jsonl"),
    ("size_bytes", True), ("size_bytes", datasets.MAX_DATASET_BYTES + 1), ("sha256", "invalid"),
    ("expected_count", 0), ("role", "practice"),
])
def test_unsafe_or_ambiguous_pin_rejected(field: str, value: object) -> None:
    pin = fixture_lock().datasets[0].to_dict()
    pin[field] = value
    with pytest.raises(ValueError):
        DatasetPin.model_validate(pin)


def test_lock_rejects_duplicates_and_oversize(tmp_path: Path) -> None:
    pin = fixture_lock().datasets[0]
    with pytest.raises(ValueError, match="duplicate"):
        DatasetLock(datasets=(pin, pin))
    path = tmp_path / "lock.json"
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="duplicate"):
        load_dataset_lock(path)
    path.write_bytes(b" " * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        load_dataset_lock(path)


def test_symlink_and_special_file_rejected(local_data: tuple[DatasetLock, Path]) -> None:
    lock, root = local_data
    path = root / "math500.jsonl"
    backup = root / "original.jsonl"
    path.rename(backup)
    path.symlink_to(backup)
    with pytest.raises(ValueError, match="symlink"):
        verify_dataset(lock, root, "math500")
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(ValueError, match="regular file"):
        verify_dataset(lock, root, "math500")


@pytest.mark.parametrize("selection", [[], ["unknown"], ["math500", "math500"]])
def test_invalid_selection_rejected(local_data: tuple[DatasetLock, Path], selection: list[str]) -> None:
    lock, root = local_data
    with pytest.raises(ValueError, match="distinct pinned"):
        verify_datasets(lock, root, dataset_names=selection)


def _network(monkeypatch: pytest.MonkeyPatch, data: bytes) -> Mock:
    opener = Mock()
    opener.open.side_effect = lambda *args, **kwargs: io.BytesIO(data)
    factory = Mock(return_value=opener)
    monkeypatch.setattr(datasets.urllib.request, "build_opener", factory)
    return factory


def test_explicit_fetch_authenticates_before_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = fixture_lock()
    network = _network(monkeypatch, encoded(ROWS["math500"]))
    result = fetch_datasets(lock, tmp_path, dataset_names=["math500"])
    assert result[0].dataset == "math500"
    assert list(tmp_path.iterdir()) == [tmp_path / "math500.jsonl"]
    fetch_datasets(lock, tmp_path, dataset_names=["math500"])
    network.assert_called_once()


@pytest.mark.parametrize("bad_bytes", [b"broken", encoded(ROWS["math500"]) + b"extra"])
def test_failed_download_never_publishes_or_leaves_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_bytes: bytes,
) -> None:
    _network(monkeypatch, bad_bytes)
    with pytest.raises(ValueError):
        fetch_datasets(fixture_lock(), tmp_path, dataset_names=["math500"])
    assert list(tmp_path.iterdir()) == []


def test_corrupt_existing_file_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    network = _network(monkeypatch, encoded(ROWS["math500"]))
    path = tmp_path / "math500.jsonl"
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError):
        fetch_datasets(fixture_lock(), tmp_path, dataset_names=["math500"])
    assert path.read_bytes() == b"corrupt"
    network.assert_not_called()


@pytest.mark.parametrize("budget", [0, -1, True, float("inf"), 10])
def test_fetch_rejects_invalid_or_insufficient_budget(tmp_path: Path, budget: Any) -> None:
    with pytest.raises(ValueError):
        fetch_datasets(fixture_lock(), tmp_path, max_download_bytes=budget)


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf"), 301])
def test_fetch_rejects_invalid_timeout(tmp_path: Path, timeout: Any) -> None:
    with pytest.raises(ValueError):
        fetch_datasets(fixture_lock(), tmp_path, timeout_seconds=timeout)


def test_overlap_audit_does_not_claim_contamination_proof() -> None:
    a = EvaluationProblem(problem_id="a", text="Ａ math\n problem", answer="1")
    b = EvaluationProblem(problem_id="b", text="a MATH problem", answer="2")
    assert overlapping_problem_ids((a,), (b,)) == (("a", "b"),)
    assert overlapping_problem_ids((a,), (b,), normalized=False) == ()
