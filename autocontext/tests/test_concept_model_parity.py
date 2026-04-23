from __future__ import annotations

import json
from pathlib import Path

from autocontext.concepts import get_concept_model


def test_concept_model_matches_shared_artifact() -> None:
    shared_model = json.loads(
        (Path(__file__).resolve().parents[2] / "docs" / "concept-model.json").read_text(encoding="utf-8")
    )

    assert get_concept_model() == shared_model
