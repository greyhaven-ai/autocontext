"""Pinned three-role fixture data with practice-only adapter projections."""

from __future__ import annotations

import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest, regular_file
from autocontext.weight_evolution.comparison_models import ComparisonCase
from autocontext.weight_evolution.models import FrozenModel, _unique_object
from autocontext.weight_evolution.session_models import PracticeProblem

MAX_SESSION_DATA_BYTES = 8 * 1024 * 1024
_ROLES = ("practice", "confirmation", "final_audit")


class SessionDataBundle(FrozenModel):
    schema_version: Literal[1] = 1
    origin: Literal["synthetic_fixture"] = "synthetic_fixture"
    practice: tuple[ComparisonCase, ...] = Field(min_length=1, max_length=1000)
    confirmation: tuple[ComparisonCase, ...] = Field(min_length=1, max_length=1000)
    final_audit: tuple[ComparisonCase, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def separated_roles(self) -> Self:
        ids: set[str] = set()
        prompts: set[str] = set()
        for role in _ROLES:
            for case in getattr(self, role):
                problem = case.problem
                normalized = " ".join(unicodedata.normalize("NFKC", problem.text).casefold().split())
                if problem.problem_id in ids:
                    raise ValueError("duplicate problem identity within or across session data roles")
                if normalized in prompts:
                    raise ValueError("normalized prompt alias within or across session data roles")
                ids.add(problem.problem_id)
                prompts.add(normalized)
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())

    @property
    def practice_cases(self) -> tuple[ComparisonCase, ...]:
        return self.practice

    def split_commitments(self) -> dict[str, dict[str, Any]]:
        """Return identities/counts, never sealed split questions or answers."""
        return {role: {"digest": canonical_digest([case.to_dict() for case in getattr(self, role)]),
                       "count": len(getattr(self, role))} for role in _ROLES}

    def practice_projection(self, problem_ids: tuple[str, ...]) -> tuple[PracticeProblem, ...]:
        """Expose selected practice prompts only, in the requested order."""
        if not problem_ids or len(problem_ids) > 1000 or len(set(problem_ids)) != len(problem_ids):
            raise ValueError("practice projection requires distinct nonempty problem IDs")
        cases = {case.problem.problem_id: case for case in self.practice}
        if not set(problem_ids) <= set(cases):
            raise ValueError("inspection/probing is restricted to the practice split")
        return tuple(PracticeProblem(dataset=cases[key].dataset, problem_id=key, text=cases[key].problem.text)
                     for key in problem_ids)

    def project_practice(self, problem_ids: tuple[str, ...]) -> tuple[PracticeProblem, ...]:
        return self.practice_projection(problem_ids)


def load_session_data(path: Path, *, expected_sha256: str, expected_size_bytes: int) -> SessionDataBundle:
    """Verify bounded raw bytes before interpreting any fixture data."""
    if (type(expected_size_bytes) is not int or not 0 < expected_size_bytes <= MAX_SESSION_DATA_BYTES
            or not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(char not in "0123456789abcdef" for char in expected_sha256)):
        raise ValueError("session data requires a finite byte count and SHA-256 pin")
    with regular_file(path) as stream:
        data = stream.read(expected_size_bytes + 1)
    if len(data) != expected_size_bytes or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("session data bytes differ from their pinned size/hash")
    value = json.loads(data, object_pairs_hook=_unique_object)
    json.dumps(value, allow_nan=False)
    return SessionDataBundle.model_validate_json(data)
