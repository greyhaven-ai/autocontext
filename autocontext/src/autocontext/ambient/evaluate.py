"""the evaluate stage: score trained candidates under the charter's frozen anchor + drift canary.

Every trained candidate is judged on its target's held-out eval suite by the anchor model
(the one guardrail model the charter cannot retrain), and a drift canary probes that anchor
for position bias. Both results are written back onto the candidate's registry metadata under
"eval" without ever changing its activation_state: this stage measures, it does not promote.
A candidate stays a "candidate" here; promotion (which refuses a drift-flagged candidate) is a
later slice that reads this eval block.

The scorer and drift probe are injected so CI runs deterministic fakes; the defaults build a
real anchor-model LLMJudge and position-bias probe. In this slice the injected scorer receives
(case.prompt, case.reference) as a stand-in for the candidate's own generation; plan 5b's real
serving wires the trained candidate's output in as the scored text.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from autocontext.ambient.charter import CharterAnchor, CharterTarget
from autocontext.ambient.eval_suite import load_eval_suite
from autocontext.ambient.stage import StageContext, StageResult
from autocontext.execution.bias_probes import BiasProbeResult, run_position_bias_probe
from autocontext.training.model_registry import DistilledModelRecord, ModelRegistry


def _default_now() -> str:
    return datetime.now(UTC).isoformat()


def eval_fingerprint(anchor: CharterAnchor, eval_suite: str) -> str:
    """A stable identity for the evaluation config a candidate was scored under.

    Combines the frozen anchor (provider, model, rubric) with the target's eval suite name. A
    candidate whose stored fingerprint no longer matches the current one was scored under a stale
    config (the charter anchor, its rubric, or the suite changed), so it must be re-evaluated rather
    than left stuck with a score computed under the old config.
    """
    rubric_hash = hashlib.sha256(anchor.rubric.encode()).hexdigest()[:16]
    return "|".join([anchor.provider, anchor.model, rubric_hash, eval_suite])


class _Scorer(Protocol):
    """Scores a candidate's output for one held-out case (0.0-1.0)."""

    def score(self, prompt: str, output: str) -> float: ...


@dataclass(slots=True)
class _JudgeScorer:
    """Adapts an LLMJudge's evaluate(...).score to the _Scorer interface."""

    judge: object  # LLMJudge; kept loose so the module has no import-time judge dependency

    def score(self, prompt: str, output: str) -> float:
        result = self.judge.evaluate(prompt, output)  # type: ignore[attr-defined]
        return float(result.score)


def _default_judge_factory(anchor: CharterAnchor) -> _Scorer:
    from autocontext.execution.judge import LLMJudge
    from autocontext.providers.registry import create_provider

    judge = LLMJudge(
        model=anchor.model,
        rubric=anchor.rubric,
        provider=create_provider(anchor.provider, model=anchor.model),
    )
    return _JudgeScorer(judge)


def _default_probe_fn(anchor: CharterAnchor) -> BiasProbeResult:
    from autocontext.providers.registry import create_provider

    provider = create_provider(anchor.provider, model=anchor.model)
    return run_position_bias_probe(
        provider=provider,
        model=anchor.model,
        system_prompt="You are the frozen anchor judge. Score fairly regardless of ordering.",
        candidate_a="A concise, correct answer.",
        candidate_b="A concise, correct answer.",
        rubric=anchor.rubric,
    )


@dataclass(slots=True)
class EvaluateStage:
    name: str
    registry: ModelRegistry
    suites_dir: Path
    judge_factory: Callable[[CharterAnchor], _Scorer] | None = None
    probe_fn: Callable[..., BiasProbeResult] | None = None
    drift_tolerance: float = 0.2
    now_fn: Callable[[], str] = _default_now
    # False because the default judge_factory scores the eval suite's reference text (a placeholder),
    # not the candidate model's own generation. it becomes True only once a real candidate-generation
    # scorer is wired (plan 5b, or a test that stands in for it). the promote stage refuses to activate
    # a candidate whose eval was a placeholder, so this flag is what makes a candidate promotable.
    scores_candidate_generation: bool = False

    def run_once(self, ctx: StageContext) -> StageResult:
        anchor = ctx.charter.anchor
        judge_factory = self.judge_factory or _default_judge_factory
        probe_fn = self.probe_fn or _default_probe_fn
        # the anchor judge is built once and reused across candidates: it is fixed per charter and
        # constructing the default provider per candidate would be wasteful. build lazily so a cycle
        # with no eligible candidate never touches the provider.
        scorer: _Scorer | None = None
        # the drift canary is a property of the fixed charter anchor, not the candidate being
        # evaluated, so it is computed once per cycle and applied to all candidates. it shares the
        # scorer's lazy-build-once pattern: both build on the first candidate that actually has a
        # suite to score, so an all-skipped or all-no-suite cycle never touches the probe provider.
        drift: BiasProbeResult | None = None

        def get_scorer() -> _Scorer:
            nonlocal scorer
            if scorer is None:
                scorer = judge_factory(anchor)
            return scorer

        def get_drift() -> BiasProbeResult:
            nonlocal drift
            if drift is None:
                drift = probe_fn(anchor)
            return drift

        processed = 0
        errors = 0
        for record in self.registry.list_all():
            if record.activation_state != "candidate":
                continue
            target = self._target_for(ctx, record)
            if target is None:
                # not one of this charter's targets: another charter (or a stale artifact) owns it,
                # so it is silently skipped rather than reported as ours.
                continue
            # the fingerprint is per-target because the eval suite is per-target, while the anchor is
            # charter-wide; compute it here where the target (and its suite) are known.
            fingerprint = eval_fingerprint(anchor, target.eval_suite)
            existing = record.metadata.get("eval")
            if existing and existing.get("fingerprint") == fingerprint:
                # already scored under the CURRENT config: skip. if the fingerprint differs (the
                # anchor, its rubric, or the suite changed), fall through and re-evaluate, overwriting
                # the stale eval rather than leaving the candidate stuck with an old-config score.
                continue
            try:
                processed += self._evaluate_candidate(ctx, record, target, anchor, fingerprint, get_scorer, get_drift)
            except Exception as exc:
                errors += 1
                ctx.emitter.emit(
                    "evaluate_candidate_failed",
                    {"artifact_id": record.artifact_id, "error": str(exc)},
                    channel="ambient",
                )
        return StageResult(processed=processed, errors=errors)

    def _evaluate_candidate(
        self,
        ctx: StageContext,
        record: DistilledModelRecord,
        target: CharterTarget,
        anchor: CharterAnchor,
        fingerprint: str,
        get_scorer: Callable[[], _Scorer],
        get_drift: Callable[[], BiasProbeResult],
    ) -> int:
        suite = load_eval_suite(self.suites_dir, target.eval_suite)
        if suite is None or not suite.cases:
            # a missing file means no held-out suite was ever defined; a present-but-empty file
            # means one exists but has nothing to score yet. either way there is nothing to judge,
            # so we report and leave the candidate unevaluated (a later cycle retries once cases land).
            ctx.emitter.emit(
                "evaluate_no_suite",
                {"artifact_id": record.artifact_id, "target": target.name, "eval_suite": target.eval_suite},
                channel="ambient",
            )
            return 0

        # v1 stand-in: the injected scorer receives (case.prompt, case.reference); plan 5b wires the
        # candidate model's real generation as the scored output.
        scorer = get_scorer()
        scores = [scorer.score(case.prompt, case.reference) for case in suite.cases]
        avg_score = sum(scores) / len(scores)

        # the drift canary depends only on the anchor, so this returns the same memoized probe for
        # every candidate in the cycle.
        probe = get_drift()
        drift_ok = probe.magnitude <= self.drift_tolerance

        record.metadata["eval"] = {
            "anchor_model": anchor.model,
            "score": avg_score,
            "drift_magnitude": probe.magnitude,
            "drift_ok": drift_ok,
            "evaluated_at": self.now_fn(),
            # whether the score judged the candidate's real generation or a placeholder (reference
            # text). the promote stage only activates a candidate when this is True.
            "from_candidate_generation": self.scores_candidate_generation,
            # identity of the config this score was computed under; a later cycle re-evaluates when
            # the current fingerprint no longer matches this one.
            "fingerprint": fingerprint,
        }
        # re-register to persist the eval block; activation_state is untouched so the candidate
        # stays a candidate — this stage never promotes.
        self.registry.register(record)

        ctx.emitter.emit(
            "evaluate_completed",
            {"artifact_id": record.artifact_id, "target": target.name, "score": avg_score, "drift_ok": drift_ok},
            channel="ambient",
        )
        return 1

    @staticmethod
    def _target_for(ctx: StageContext, record: DistilledModelRecord) -> CharterTarget | None:
        target_name = record.metadata.get("target")
        for target in ctx.charter.targets:
            if target.name == target_name:
                return target
        return None
