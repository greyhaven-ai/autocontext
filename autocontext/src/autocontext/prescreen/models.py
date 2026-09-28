"""The pre-screen model ladder (design section 3). Needs the `prescreen` extra (scikit-learn, numpy)."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import numpy as np
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion
from sklearn.preprocessing import StandardScaler

from autocontext.prescreen.dataset import Example

TEXT_WEIGHTS = (0.25, 0.5, 1.0, 2.0, 4.0)
C_GRID = (0.1, 1.0, 10.0)
SMOOTHING = 1e-3


class PrescreenModel(Protocol):
    name: str

    def fit(self, train: Sequence[Example]) -> None: ...

    def predict_fail(self, examples: Sequence[Example]) -> list[float]: ...


def log_loss(p_fail: Sequence[float], failed: Sequence[bool]) -> float:
    """Mean negative log-likelihood of the judge's verdicts, smoothed so a confident miss stays finite."""
    total = 0.0
    for p, y in zip(p_fail, failed, strict=True):
        q = (1 - SMOOTHING) * p + SMOOTHING / 2
        total -= math.log(q if y else 1 - q)
    return total / len(p_fail)


def _labels(examples: Sequence[Example]) -> list[int]:
    return [int(e.failed) for e in examples]


def _one_class(train: Sequence[Example]) -> bool:
    return len({e.failed for e in train}) < 2


def _fail_column(model: Any, rows: Any) -> list[float]:
    column = list(model.classes_).index(1)
    return [float(p) for p in model.predict_proba(rows)[:, column]]


def _text_features() -> FeatureUnion:
    return FeatureUnion(
        [
            ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True)),
        ]
    )


class BaseRateModel:
    name = "p0-base-rate"

    def __init__(self) -> None:
        self.rate = 0.5

    def fit(self, train: Sequence[Example]) -> None:
        self.rate = sum(e.failed for e in train) / len(train) if train else 0.5

    def predict_fail(self, examples: Sequence[Example]) -> list[float]:
        return [self.rate] * len(examples)


class StructuralModel:
    name = "p1-structural"

    def __init__(self) -> None:
        self.base = BaseRateModel()
        self.model: Any = None

    def fit(self, train: Sequence[Example]) -> None:
        self.base.fit(train)
        self.model = None
        if _one_class(train):
            return
        x = np.array([e.structural for e in train], dtype=float)
        self.scaler = StandardScaler().fit(x)
        self.model = LogisticRegression(C=1.0, max_iter=2000).fit(self.scaler.transform(x), _labels(train))

    def predict_fail(self, examples: Sequence[Example]) -> list[float]:
        if self.model is None:
            return self.base.predict_fail(examples)
        x = np.array([e.structural for e in examples], dtype=float)
        return _fail_column(self.model, self.scaler.transform(x))


class TextModel:
    name = "p2-text"

    def __init__(self) -> None:
        self.base = BaseRateModel()
        self.model: Any = None

    def fit(self, train: Sequence[Example]) -> None:
        self.base.fit(train)
        self.model = None
        if _one_class(train):
            return
        texts = [e.text for e in train]
        self.text = _text_features().fit(texts)
        self.model = LogisticRegression(C=1.0, max_iter=2000).fit(self.text.transform(texts), _labels(train))

    def predict_fail(self, examples: Sequence[Example]) -> list[float]:
        if self.model is None:
            return self.base.predict_fail(examples)
        return _fail_column(self.model, self.text.transform([e.text for e in examples]))


class HybridModel:
    name = "p3-hybrid"

    def __init__(self) -> None:
        self.base = BaseRateModel()
        self.model: Any = None
        self.weight, self.c = 1.0, 1.0

    def _blocks(self, examples: Sequence[Example], *, fit: bool) -> tuple[Any, Any]:
        x = np.array([e.structural for e in examples], dtype=float)
        texts = [e.text for e in examples]
        if fit:
            self.scaler = StandardScaler().fit(x)
            self.text = _text_features().fit(texts)
        return sparse.csr_matrix(self.scaler.transform(x)), self.text.transform(texts)

    def _choose(self, train: Sequence[Example]) -> tuple[float, float]:
        """(text weight, C) with the lowest log-loss on the last 30% of the time-ordered window (the R6 recipe)."""
        cut = int(len(train) * 0.7)
        head, tail = train[:cut], train[cut:]
        if not tail or _one_class(head):
            return 1.0, 1.0
        head_s, head_t = self._blocks(head, fit=True)
        tail_s, tail_t = self._blocks(tail, fit=False)
        best = (math.inf, 1.0, 1.0)
        for weight in TEXT_WEIGHTS:
            for c in C_GRID:
                model = LogisticRegression(C=c, max_iter=2000).fit(
                    sparse.hstack([head_s, weight * head_t]).tocsr(), _labels(head)
                )
                p = _fail_column(model, sparse.hstack([tail_s, weight * tail_t]).tocsr())
                loss = log_loss(p, [e.failed for e in tail])
                if loss < best[0]:
                    best = (loss, weight, c)
        return best[1], best[2]

    def fit(self, train: Sequence[Example]) -> None:
        self.base.fit(train)
        self.model = None
        if _one_class(train):
            return
        self.weight, self.c = self._choose(train)
        structural, text = self._blocks(train, fit=True)
        self.model = LogisticRegression(C=self.c, max_iter=2000).fit(
            sparse.hstack([structural, self.weight * text]).tocsr(), _labels(train)
        )

    def predict_fail(self, examples: Sequence[Example]) -> list[float]:
        if self.model is None:
            return self.base.predict_fail(examples)
        structural, text = self._blocks(examples, fit=False)
        return _fail_column(self.model, sparse.hstack([structural, self.weight * text]).tocsr())


MODEL_FACTORIES: dict[str, Callable[[], PrescreenModel]] = {
    "p0": BaseRateModel,
    "p1": StructuralModel,
    "p2": TextModel,
    "p3": HybridModel,
}


def build_model(key: str) -> PrescreenModel:
    try:
        return MODEL_FACTORIES[key]()
    except KeyError:
        raise ValueError(f"unknown pre-screen model {key!r}; choose from {', '.join(MODEL_FACTORIES)}") from None
