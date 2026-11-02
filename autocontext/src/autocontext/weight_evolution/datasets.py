"""Pinned benchmark bytes and deterministic, data-only evaluation cohorts.

This module never imports a dataset script or exposes answers to inference.
The normalized digest covers ordered prompts, gold answers and instructions;
the inference adapter must explicitly project away the scoring-only fields.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import unicodedata
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest, regular_file
from autocontext.weight_evolution.assets import _HTTPSRedirectHandler
from autocontext.weight_evolution.models import Digest, FrozenModel, _unique_object

DatasetName = Literal["math500", "gsm8k", "ifeval"]
MAX_DATASET_BYTES = 16 * 1024 * 1024
_SOURCES = {
    "math500": ("HuggingFaceH4/MATH-500", "test.jsonl", "https://huggingface.co/datasets"),
    "gsm8k": ("openai/grade-school-math", "grade_school_math/data/test.jsonl", "https://raw.githubusercontent.com"),
    "ifeval": (
        "google-research/google-research", "instruction_following_eval/data/input_data.jsonl",
        "https://raw.githubusercontent.com",
    ),
}


def _nonblank(value: str) -> str:
    if not value.strip() or "\0" in value:
        raise ValueError("text must be nonblank and contain no NUL")
    return value


class DatasetInstruction(FrozenModel):
    instruction_id: str = Field(min_length=1, max_length=256)
    kwargs: dict[str, JsonValue]

    @field_validator("instruction_id")
    @classmethod
    def instruction_identity(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("instruction identity must be trimmed and contain no control characters")
        return _nonblank(value)

    @field_validator("kwargs")
    @classmethod
    def bounded_arguments(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if len(json.dumps(value, allow_nan=False).encode()) > 16384:
            raise ValueError("instruction arguments exceed 16 KiB")
        return value


class EvaluationProblem(FrozenModel):
    problem_id: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1, max_length=131072)
    answer: str | None = Field(default=None, max_length=131072)
    instructions: tuple[DatasetInstruction, ...] = Field(default=(), max_length=32)

    @field_validator("problem_id")
    @classmethod
    def canonical_identity(cls, value: str) -> str:
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("problem identity must be trimmed and contain no control characters")
        return _nonblank(value)

    @field_validator("text", "answer")
    @classmethod
    def nonblank_text(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value)

    @model_validator(mode="after")
    def scoring_payload(self) -> Self:
        if (self.answer is not None) == bool(self.instructions):
            raise ValueError("each problem requires either a math answer or IFEval instructions")
        return self


class DatasetPin(FrozenModel):
    dataset: DatasetName
    repository: str
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_path: str
    url: str
    relative_path: str
    sha256: Digest
    size_bytes: int = Field(gt=0, le=MAX_DATASET_BYTES)
    expected_count: int = Field(gt=0, le=100000)
    normalized_sha256: Digest
    split: Literal["test"] = "test"
    format: Literal["jsonl"] = "jsonl"
    role: Literal["final_test"] = "final_test"
    normalization: Literal["qwen-reproduction-cohort-v1"] = "qwen-reproduction-cohort-v1"

    @model_validator(mode="after")
    def source_identity(self) -> Self:
        repository, path, origin = _SOURCES[self.dataset]
        revision_prefix = "resolve/" if self.dataset == "math500" else ""
        expected_url = f"{origin}/{repository}/{revision_prefix}{self.revision}/{path}"
        if self.repository != repository or self.source_path != path or self.url != expected_url:
            raise ValueError("dataset source must match its immutable official-source URL")
        if self.relative_path != f"{self.dataset}.jsonl":
            raise ValueError("dataset destination must be its canonical JSONL filename")
        return self


class DatasetLock(FrozenModel):
    schema_version: Literal[1] = 1
    datasets: tuple[DatasetPin, ...] = Field(min_length=1, max_length=3)
    fidelity_notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def distinct_datasets(self) -> Self:
        if len({pin.dataset for pin in self.datasets}) != len(self.datasets):
            raise ValueError("duplicate dataset pin")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


@dataclass(frozen=True)
class VerifiedDataset:
    dataset: DatasetName
    digest: str
    problems: tuple[EvaluationProblem, ...]
    source_sha256: str
    lock_digest: str

    @property
    def dataset_name(self) -> str:
        return self.dataset

    def to_dict(self) -> dict[str, object]:
        """Verification summary deliberately omits protected prompts and answers."""
        return {
            "dataset": self.dataset, "digest": self.digest, "source_sha256": self.source_sha256,
            "lock_digest": self.lock_digest, "n_problems": len(self.problems), "ok": True,
            "role": "final_test", "normalization": "qwen-reproduction-cohort-v1",
        }


def load_dataset_lock(path: Path) -> DatasetLock:
    with regular_file(path) as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise ValueError("dataset lock exceeds 64 KiB")
    json.loads(data, object_pairs_hook=_unique_object)
    return DatasetLock.model_validate_json(data)


def _source_rows(data: bytes) -> list[dict[str, Any]]:
    if len(data) > MAX_DATASET_BYTES:
        raise ValueError("dataset exceeds byte budget")
    rows = []
    for index, line in enumerate(data.splitlines()):
        if not line.strip():
            raise ValueError(f"blank dataset row: {index}")
        value = json.loads(line, object_pairs_hook=_unique_object)
        if not isinstance(value, dict):
            raise ValueError(f"dataset row is not an object: {index}")
        rows.append(value)
    if not rows:
        raise ValueError("dataset is empty")
    return rows


def normalize_dataset(dataset: DatasetName, data: bytes) -> tuple[EvaluationProblem, ...]:
    """Use source-faithful problem IDs/order and explicit scorer-only fields."""
    if dataset not in _SOURCES:
        raise ValueError("unknown dataset")
    problems: list[EvaluationProblem] = []
    for index, row in enumerate(_source_rows(data)):
        required = {
            "math500": {"unique_id", "problem", "answer"}, "gsm8k": {"question", "answer"},
            "ifeval": {"key", "prompt", "instruction_id_list", "kwargs"},
        }[dataset]
        if not required.issubset(row):
            raise ValueError(f"dataset row lacks required fields: {index}")
        if dataset == "math500":
            problem = EvaluationProblem(problem_id=row["unique_id"], text=row["problem"], answer=row["answer"])
        elif dataset == "gsm8k":
            answer = row["answer"]
            if not isinstance(answer, str) or answer.count("####") != 1:
                raise ValueError("GSM8K answer must contain one final-answer separator")
            problem = EvaluationProblem(
                problem_id=f"gsm8k_test_{index}", text=row["question"],
                answer=answer.rsplit("####", 1)[1].strip().replace(",", ""),
            )
        else:
            key, ids, kwargs = row["key"], row["instruction_id_list"], row["kwargs"]
            if type(key) is not int or key < 0:
                raise ValueError("IFEval source key must be a nonnegative integer")
            if not isinstance(ids, list) or not isinstance(kwargs, list) or len(ids) != len(kwargs) or not ids:
                raise ValueError("IFEval instruction IDs and kwargs must have matching nonzero counts")
            problem = EvaluationProblem(
                problem_id=f"ifeval_{key}", text=row["prompt"],
                instructions=tuple(
                    DatasetInstruction(instruction_id=name, kwargs=args) for name, args in zip(ids, kwargs, strict=True)
                ),
            )
        problems.append(problem)
    if len({problem.problem_id for problem in problems}) != len(problems):
        raise ValueError("duplicate problem identity in dataset")
    return tuple(problems)


def cohort_digest(problems: Sequence[EvaluationProblem]) -> str:
    return canonical_digest([problem.to_dict() for problem in problems])


def _select(lock: DatasetLock, dataset_names: Iterable[str] | None) -> tuple[DatasetPin, ...]:
    if dataset_names is None:
        return lock.datasets
    names = tuple(dataset_names)
    available = {pin.dataset: pin for pin in lock.datasets}
    if not names or len(set(names)) != len(names) or any(name not in available for name in names):
        raise ValueError("select one or more distinct pinned dataset names")
    return tuple(available[name] for name in names)  # type: ignore[index]


def _destination(root: Path, pin: DatasetPin) -> Path:
    path = root.resolve() / pin.relative_path
    if path.is_symlink():
        raise ValueError("symlink dataset files are not permitted")
    return path


def _read_verified(path: Path, pin: DatasetPin) -> bytes:
    with regular_file(path) as stream:
        data = stream.read(pin.size_bytes + 1)
    if len(data) != pin.size_bytes or hashlib.sha256(data).hexdigest() != pin.sha256:
        raise ValueError(f"dataset size/SHA-256 differs from its pin: {pin.dataset}")
    return data


def verify_dataset(lock: DatasetLock, root: Path, dataset: str) -> VerifiedDataset:
    pin = _select(lock, (dataset,))[0]
    data = _read_verified(_destination(root, pin), pin)
    problems = normalize_dataset(pin.dataset, data)
    digest = cohort_digest(problems)
    if len(problems) != pin.expected_count or digest != pin.normalized_sha256:
        raise ValueError(f"normalized cohort differs from pinned count/digest: {pin.dataset}")
    return VerifiedDataset(pin.dataset, digest, problems, pin.sha256, lock.digest)


def verify_datasets(
    lock: DatasetLock, root: Path, *, dataset_names: Iterable[str] | None = None,
) -> tuple[VerifiedDataset, ...]:
    return tuple(verify_dataset(lock, root, pin.dataset) for pin in _select(lock, dataset_names))


def fetch_datasets(
    lock: DatasetLock, root: Path, *, dataset_names: Iterable[str] | None = None,
    max_download_bytes: int = MAX_DATASET_BYTES, timeout_seconds: float = 30.0,
) -> tuple[VerifiedDataset, ...]:
    """Explicitly download bounded data, authenticate, then publish atomically."""
    if type(max_download_bytes) is not int or max_download_bytes < 1:
        raise ValueError("dataset download budget must be a positive integer")
    if isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
        raise ValueError("dataset timeout must be finite and between zero and 300 seconds")
    pins = _select(lock, dataset_names)
    if sum(pin.size_bytes for pin in pins) > max_download_bytes:
        raise ValueError("selected datasets exceed the download byte budget")
    root.mkdir(parents=True, exist_ok=True)
    for pin in pins:
        destination = _destination(root, pin)
        if destination.exists():
            verify_dataset(lock, root, pin.dataset)
            continue
        fd, name = tempfile.mkstemp(prefix=".dataset-", suffix=".partial", dir=root)
        os.close(fd)
        temporary = Path(name)
        try:
            opener = urllib.request.build_opener(_HTTPSRedirectHandler())
            request = urllib.request.Request(pin.url, headers={"Accept-Encoding": "identity"})
            with opener.open(request, timeout=timeout_seconds) as response, temporary.open("wb") as stream:
                remaining = pin.size_bytes + 1
                while chunk := response.read(min(1024 * 1024, remaining)):
                    stream.write(chunk)
                    remaining -= len(chunk)
                    if remaining <= 0:
                        raise ValueError("dataset download exceeds pinned size")
            data = _read_verified(temporary, pin)
            problems = normalize_dataset(pin.dataset, data)
            if len(problems) != pin.expected_count or cohort_digest(problems) != pin.normalized_sha256:
                raise ValueError("downloaded dataset normalizes to an unexpected cohort")
            try:
                os.link(temporary, destination)
            except FileExistsError:
                verify_dataset(lock, root, pin.dataset)
        finally:
            temporary.unlink(missing_ok=True)
    return verify_datasets(lock, root, dataset_names=(pin.dataset for pin in pins))


def overlapping_problem_ids(
    left: Sequence[EvaluationProblem], right: Sequence[EvaluationProblem], *, normalized: bool = True,
) -> tuple[tuple[str, str], ...]:
    """Diagnose exact/NFKC whitespace-insensitive overlap; not a contamination proof."""
    def key(text: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", text).casefold().split()) if normalized else text

    right_ids: dict[str, list[str]] = {}
    for problem in right:
        right_ids.setdefault(key(problem.text), []).append(problem.problem_id)
    return tuple((problem.problem_id, other) for problem in left for other in right_ids.get(key(problem.text), ()))
