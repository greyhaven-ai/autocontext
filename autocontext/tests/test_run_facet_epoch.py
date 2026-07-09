from __future__ import annotations

from autocontext.analytics.extractor import FacetExtractor


def _gen(idx: int, best: float, epoch: str | None) -> dict[str, object]:
    return {
        "generation_index": idx,
        "best_score": best,
        "elo": 0.0,
        "gate_decision": "advance",
        "duration_seconds": 0.0,
        "evaluator_epoch": epoch,
    }


def test_facet_epoch_is_best_scoring_rows_epoch() -> None:
    data = {
        "run": {"run_id": "r", "scenario": "s"},
        "generations": [
            _gen(1, 0.4, "e-lo"),
            _gen(2, 0.9, "e-hi"),
            _gen(3, 0.5, "e-mid"),
        ],
    }
    facet = FacetExtractor().extract(data)
    assert facet.evaluator_epoch == "e-hi"


def test_facet_epoch_null_when_tournament() -> None:
    data = {
        "run": {"run_id": "r", "scenario": "s"},
        "generations": [_gen(1, 0.4, None), _gen(2, 0.9, None)],
    }
    facet = FacetExtractor().extract(data)
    assert facet.evaluator_epoch is None
