"""the promote stage: activate an anchor-winning, drift-clean candidate into the live binding.

This is the slice that reads the eval block written by the evaluate stage and, gated by the
charter's autonomy dial, flips the best candidate to the live serving binding. Promotion refuses
a drift-flagged candidate outright (a higher score never buys past a tripped drift canary), and it
only promotes when the candidate beats the incumbent's recorded eval score under the SAME anchor.
An incumbent scored under a different (or absent) anchor is not comparable, so v1 declines to
auto-promote across mismatched anchors and emits promote_anchor_mismatch instead.

On promote the new record gets a "probation" marker naming the previous active artifact, then
registry.activate demotes that incumbent to disabled, kept warm as the rollback target. This
stage never generates or scores; it reads eval metadata and moves the activation pointer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from autocontext.ambient.charter import CharterTarget
from autocontext.ambient.eligibility import split_role_selector
from autocontext.ambient.policy import decide
from autocontext.ambient.serving_manifest import write_serving_entry
from autocontext.ambient.stage import StageContext, StageResult
from autocontext.training.model_registry import DistilledModelRecord, ModelRegistry


def _default_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class PromoteStage:
    name: str
    registry: ModelRegistry
    now_fn: Callable[[], str] = _default_now
    # opt-in (AC-893): when set, record the (real_scenario, role) -> ambient target mapping so the
    # serving resolver can bridge to a model slotted under the target name (AC-884 anti-collision).
    # None (the default) leaves the promote path byte-unchanged: nothing is written.
    serving_manifest_path: Path | None = None

    def run_once(self, ctx: StageContext) -> StageResult:
        processed = 0
        errors = 0
        for target in ctx.charter.targets:
            try:
                processed += self._promote_target(ctx, target)
            except Exception as exc:
                errors += 1
                ctx.emitter.emit(
                    "promote_target_failed",
                    {"target": target.name, "error": str(exc)},
                    channel="ambient",
                )
        return StageResult(processed=processed, errors=errors)

    def _promote_target(self, ctx: StageContext, target: CharterTarget) -> int:
        records = self.registry.list_all()
        candidates = [r for r in records if self._is_promotable_candidate(r, target.name)]
        # only compare candidates scored under the CURRENT charter anchor: if the anchor
        # changed between training cycles, candidates carrying scores from a stale anchor are
        # not apples-to-apples, so they are not eligible to be picked as best this cycle. the
        # evaluate stage now re-scores such candidates under the new anchor on a later cycle (it
        # re-evaluates whenever the eval fingerprint changes), so a stale-anchor candidate becomes
        # eligible again once re-scored. the INCUMBENT (an active record) is still not re-scored, so
        # a post-anchor-change promote can still anchor-mismatch against a stale incumbent; that
        # incumbent-side re-evaluation is tracked in AC-883 and is out of scope here.
        current_anchor = ctx.charter.anchor.model
        candidates = [r for r in candidates if r.metadata["eval"].get("anchor_model") == current_anchor]
        if not candidates:
            # nothing evaluated-and-clean under the current anchor for this target this cycle:
            # stay quiet, same as having no candidates at all.
            return 0

        best = max(candidates, key=lambda r: r.metadata["eval"]["score"])
        incumbent = self._incumbent(records, target.name)

        if not self._beats_incumbent(ctx, best, incumbent, target):
            # either the incumbent was scored under a different anchor (mismatch already emitted)
            # or the candidate does not clear the incumbent's score: leave the binding untouched.
            return 0

        decision = decide(ctx.charter, "promote", target.name)
        if decision.requires_approval:
            ctx.emitter.emit(
                "promote_requires_approval",
                {"target": target.name, "artifact_id": best.artifact_id, "reason": decision.reason},
                channel="ambient",
            )
            return 0

        previous_active = incumbent.artifact_id if incumbent is not None else ""
        best.metadata["probation"] = {"promoted_at": self.now_fn(), "previous_active": previous_active}
        # persist the probation marker while best is still a candidate; activate flips it to active
        # and demotes the incumbent to disabled, kept warm as the rollback target.
        self.registry.register(best)
        self.registry.activate(best.artifact_id)

        if self.serving_manifest_path is not None:
            # the registry slots the ambient record under the target name (AC-884), but generation
            # resolves by the real scenario, so record the (scenario, role) -> target bridge. A bare
            # role selector serves every scenario, held under the "*" bucket.
            role, scenario = split_role_selector(target.selector)
            write_serving_entry(
                self.serving_manifest_path,
                scenario=scenario or "*",
                role=role,
                target_name=target.name,
                artifact_id=best.artifact_id,
                backend=best.backend,
            )

        ctx.emitter.emit(
            "promote_activated",
            {
                "target": target.name,
                "artifact_id": best.artifact_id,
                "previous_active": previous_active,
                "score": best.metadata["eval"]["score"],
            },
            channel="ambient",
        )
        return 1

    @staticmethod
    def _is_promotable_candidate(record: DistilledModelRecord, target_name: str) -> bool:
        if record.activation_state != "candidate" or record.metadata.get("target") != target_name:
            return False
        eval_meta = record.metadata.get("eval")
        if not isinstance(eval_meta, dict):
            return False
        # until real candidate generation is wired (plan 5b), evaluation scores a placeholder (the
        # eval suite's reference text, not the candidate's own output), so promotion must refuse those
        # candidates rather than activate a model on a score that never ran it. only a candidate whose
        # eval judged its real generation (from_candidate_generation True) is promotable.
        if eval_meta.get("from_candidate_generation") is not True:
            return False
        # a drift-flagged candidate is never promotable, however high its score.
        return eval_meta.get("drift_ok") is True

    @staticmethod
    def _incumbent(records: list[DistilledModelRecord], target_name: str) -> DistilledModelRecord | None:
        for record in records:
            if record.activation_state == "active" and record.metadata.get("target") == target_name:
                return record
        return None

    def _beats_incumbent(
        self,
        ctx: StageContext,
        best: DistilledModelRecord,
        incumbent: DistilledModelRecord | None,
        target: CharterTarget,
    ) -> bool:
        if incumbent is None:
            return True
        incumbent_eval = incumbent.metadata.get("eval")
        best_anchor = best.metadata["eval"].get("anchor_model")
        if isinstance(incumbent_eval, dict) and incumbent_eval.get("anchor_model") == best_anchor:
            return bool(best.metadata["eval"]["score"] > incumbent_eval["score"])
        # v1 rule: never auto-promote across mismatched (or absent) anchors: the scores are not
        # comparable, so a human decides.
        ctx.emitter.emit(
            "promote_anchor_mismatch",
            {"target": target.name, "artifact_id": best.artifact_id},
            channel="ambient",
        )
        return False
