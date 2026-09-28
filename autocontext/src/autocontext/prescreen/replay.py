"""Offline replay of the pre-screen ladder (design section 5 and the Phase 1 gate). Needs the `prescreen` extra."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import mean
from typing import Any

from sklearn.metrics import roc_auc_score

from autocontext.harness.benchmark_stats import wilson_interval
from autocontext.prescreen.calibrate import MIN_COVERED, TARGET_PRECISION, inner_skip_threshold
from autocontext.prescreen.dataset import Example, feature_spec_hash
from autocontext.prescreen.models import build_model, log_loss
from autocontext.prescreen.stats import cluster_mean_difference_interval, cluster_ratio_interval

GATE_MIN_SKIP_RATE = 0.20
GATE_MIN_PRECISION = 0.95
GATE_MIN_PRECISION_LOW = 0.90
BASELINE = "p1-structural"


@dataclass(frozen=True)
class Scored:
    model: str
    fold: int
    row_id: int
    group: str
    failed: bool
    p_fail: float
    threshold: float | None

    @property
    def skipped(self) -> bool:
        return self.threshold is not None and self.p_fail >= self.threshold


def expanding_folds(
    examples: Sequence[Example], *, blocks: int = 5, min_train: int = 100
) -> list[tuple[int, list[Example], list[Example]]]:
    """Train on every earlier block and test on the next; folds with fewer than min_train training rounds are dropped."""
    size = math.ceil(len(examples) / blocks) if examples else 0
    folds: list[tuple[int, list[Example], list[Example]]] = []
    for k in range(1, blocks):
        train, test = list(examples[: k * size]), list(examples[k * size : (k + 1) * size])
        if len(train) >= min_train and test:
            folds.append((k, train, test))
    return folds


def score_folds(
    examples: Sequence[Example],
    model_keys: Sequence[str],
    *,
    blocks: int,
    min_train: int,
    target: float,
    min_covered: int,
) -> list[Scored]:
    scored: list[Scored] = []
    for fold, train, test in expanding_folds(examples, blocks=blocks, min_train=min_train):
        for key in model_keys:
            # key=key binds the loop variable (ruff B023); mypy cannot infer a defaulted lambda param here (known limitation).
            threshold = inner_skip_threshold(
                lambda key=key: build_model(key),  # type: ignore[misc]
                train,
                target=target,
                min_covered=min_covered,
            )
            model = build_model(key)
            model.fit(train)
            for example, p in zip(test, model.predict_fail(test), strict=True):
                scored.append(Scored(model.name, fold, example.row_id, example.group, example.failed, p, threshold))
    return scored


def summarize(scored: Sequence[Scored], *, seed: int, resamples: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in dict.fromkeys(s.model for s in scored):
        mine = [s for s in scored if s.model == name]
        failed = [s.failed for s in mine]
        p = [s.p_fail for s in mine]
        groups = [s.group for s in mine]
        skipped = [s for s in mine if s.skipped]
        hits = sum(s.failed for s in skipped)
        both = 0 < sum(failed) < len(failed)
        rows.append(
            {
                "model": name,
                "n": len(mine),
                "failing_rate": mean(failed),
                "agreement": mean((pf >= 0.5) == y for pf, y in zip(p, failed, strict=True)),
                "log_loss": log_loss(p, failed),
                "auroc": float(roc_auc_score(failed, p)) if both else None,
                "skipped": len(skipped),
                "skip_rate": len(skipped) / len(mine),
                "skip_rate_interval": cluster_ratio_interval(
                    [float(s.skipped) for s in mine], [1.0] * len(mine), groups, seed=seed, resamples=resamples
                ),
                "skip_precision": hits / len(skipped) if skipped else None,
                "skip_precision_wilson": wilson_interval(hits, len(skipped)),
                "skip_precision_interval": cluster_ratio_interval(
                    [float(s.skipped and s.failed) for s in mine],
                    [float(s.skipped) for s in mine],
                    groups,
                    seed=seed,
                    resamples=resamples,
                ),
            }
        )
    return rows


def _example_loss(s: Scored) -> float:
    return log_loss([s.p_fail], [s.failed])


def compare_to_baseline(scored: Sequence[Scored], *, seed: int, resamples: int) -> list[dict[str, Any]]:
    """Paired per-round log-loss difference (model minus P1), with a task-clustered interval."""
    base = {(s.fold, s.row_id): s for s in scored if s.model == BASELINE}
    if not base:
        return []
    out: list[dict[str, Any]] = []
    for name in dict.fromkeys(s.model for s in scored if s.model != BASELINE):
        pairs = [(s, base[(s.fold, s.row_id)]) for s in scored if s.model == name and (s.fold, s.row_id) in base]
        a = [_example_loss(s) for s, _ in pairs]
        b = [_example_loss(t) for _, t in pairs]
        out.append(
            {
                "model": name,
                "baseline": BASELINE,
                "n": len(pairs),
                "log_loss_difference": mean(a) - mean(b) if pairs else None,
                "interval": cluster_mean_difference_interval(a, b, [s.group for s, _ in pairs], seed=seed, resamples=resamples)
                if pairs
                else None,
            }
        )
    return out


def _qualifies(row: dict[str, Any]) -> bool:
    wilson = row["skip_precision_wilson"]
    return (
        row["skip_rate"] >= GATE_MIN_SKIP_RATE
        and row["skip_precision"] is not None
        and row["skip_precision"] >= GATE_MIN_PRECISION
        and wilson is not None
        and wilson[0] >= GATE_MIN_PRECISION_LOW
    )


def phase1_gate(summary: Sequence[dict[str, Any]], comparisons: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Design Phase 1: skip >= 20% of eligible calls at precision >= 0.95 (Wilson low >= 0.90); prefer P1 unless beaten."""
    if not any(r["model"] == BASELINE for r in summary):
        return {"go": False, "model": None, "reason": "the Phase 1 gate needs P1 (structural) in the ladder"}
    candidates = sorted((r for r in summary if _qualifies(r)), key=lambda r: (-r["skip_rate"], r["log_loss"]))
    if not candidates:
        return {"go": False, "model": None, "reason": "no model skips at least 20% of eligible calls at the precision bar"}
    best = candidates[0]
    if best["model"] == BASELINE:
        return {"go": True, "model": BASELINE, "reason": "P1 meets the skip bar"}
    comparison = next((c for c in comparisons if c["model"] == best["model"]), None)
    if comparison is not None and comparison["interval"] is not None and comparison["interval"][1] < 0:
        return {"go": True, "model": best["model"], "reason": f"{best['model']} meets the skip bar and beats P1 on log-loss"}
    if any(c["model"] == BASELINE for c in candidates):
        return {
            "go": True,
            "model": BASELINE,
            "reason": f"{best['model']} does not beat P1 on log-loss; P1 is carried forward as the simpler model",
        }
    return {
        "go": True,
        "model": best["model"],
        "reason": f"{best['model']} meets the skip bar; P1 does not, and the log-loss comparison is inconclusive",
    }


def learning_curve(
    examples: Sequence[Example],
    key: str,
    *,
    holdout_fraction: float = 0.25,
    halvings: int = 3,
    repeats: int = 5,
    seed: int = 0,
    target: float = TARGET_PRECISION,
    min_covered: int = MIN_COVERED,
) -> list[dict[str, Any]]:
    """Fit on growing slices of the pool and score one fixed, most-recent holdout: random samples against the most recent."""
    cut = int(len(examples) * (1 - holdout_fraction))
    pool, holdout = list(examples[:cut]), list(examples[cut:])
    if not pool or not holdout:
        return []
    halves = {len(pool) // 2**k for k in range(halvings, 0, -1)}
    sizes = sorted({n for n in halves if n >= 2 * min_covered} | {len(pool)})
    truth = [e.failed for e in holdout]
    rows: list[dict[str, Any]] = []
    for n in sizes:
        sets: list[tuple[str, int, list[Example]]] = []
        if n == len(pool):
            sets.append(("all", 0, pool))
        else:
            for repeat in range(repeats):
                picks = sorted(random.Random(f"{seed}:{n}:{repeat}").sample(range(len(pool)), n))
                sets.append(("random", repeat, [pool[i] for i in picks]))
            sets.append(("recent", 0, pool[-n:]))
        for scheme, repeat, train in sets:
            threshold = inner_skip_threshold(lambda: build_model(key), train, target=target, min_covered=min_covered)
            model = build_model(key)
            model.fit(train)
            p = model.predict_fail(holdout)
            skipped = [threshold is not None and pf >= threshold for pf in p]
            n_skipped = sum(skipped)
            hits = sum(f for f, s in zip(truth, skipped, strict=True) if s)
            rows.append(
                {
                    "model": model.name,
                    "scheme": scheme,
                    "n": n,
                    "repeat": repeat,
                    "log_loss": log_loss(p, truth),
                    "skip_rate": n_skipped / len(p),
                    "skip_precision": hits / n_skipped if n_skipped else None,
                }
            )
    return rows


def run_replay(
    examples: Sequence[Example],
    *,
    family: str,
    epoch: str,
    model_keys: Sequence[str] = ("p0", "p1", "p2", "p3"),
    curve_model: str | None = "p3",
    blocks: int = 5,
    min_train: int = 100,
    target: float = TARGET_PRECISION,
    min_covered: int = MIN_COVERED,
    curve_halvings: int = 3,
    curve_repeats: int = 5,
    seed: int = 0,
    resamples: int = 2000,
) -> dict[str, Any]:
    scored = score_folds(examples, model_keys, blocks=blocks, min_train=min_train, target=target, min_covered=min_covered)
    summary = summarize(scored, seed=seed, resamples=resamples)
    comparisons = compare_to_baseline(scored, seed=seed, resamples=resamples)
    curve = (
        learning_curve(
            examples,
            curve_model,
            halvings=curve_halvings,
            repeats=curve_repeats,
            seed=seed,
            target=target,
            min_covered=min_covered,
        )
        if curve_model
        else []
    )
    return {
        "meta": {
            "family": family,
            "epoch": epoch,
            "examples": len(examples),
            "groups": len({e.group for e in examples}),
            "from": examples[0].created_at if examples else None,
            "to": examples[-1].created_at if examples else None,
            "feature_spec_hash": feature_spec_hash(),
            "models": list(model_keys),
            "folds": len({s.fold for s in scored}),
            "blocks": blocks,
            "min_train": min_train,
            "target_precision": target,
            "min_covered": min_covered,
            "seed": seed,
            "resamples": resamples,
        },
        "summary": summary,
        "comparisons": comparisons,
        "gate": phase1_gate(summary, comparisons),
        "learning_curve": curve,
    }
