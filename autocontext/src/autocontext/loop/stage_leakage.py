"""Opt-in leakage stage: runs the verified-mode leakage gate in the pipeline (AC-879).

This is the pre-scoring seam for the AC-879 leakage gate, mirroring the AC-878
repair-gate seam. It sits alongside :func:`stage_repair` and is caller-gated: it
early-returns unless :func:`leakage_gate_active_for` says the gate is active for
this scenario, so with the default flags OFF the generation path is
byte-unchanged (this stage is a no-op that touches nothing on ``ctx``, reads
nothing, and emits no events).

When active, it looks for declared integrity metadata under
``ctx.current_strategy["__integrity_metadata__"]`` and observed access records
under ``ctx.current_strategy["__access_records__"]``. It builds an
:class:`IntegrityMetadata`, runs :func:`audit_leakage`, evaluates the gate via
:func:`evaluate_leakage_gate`, and records the decision onto
``ctx.exploration_metadata["leakage_gate"]`` plus one telemetry event on the
``leakage`` channel. When the gate fails closed it appends ``leakage_blocked``
to ``ctx.gate_decision_history``.

No production stage attaches the integrity-metadata key yet, so the stage is
opt-in telemetry until a producer is added (a documented follow-up, consistent
with the AC-878 deferred-producer pattern). Full advancement enforcement (acting
on a blocked decision to halt promotion) is likewise a follow-up: this stage
records the gate decision and telemetry, opt-in and default-off.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from autocontext.harness_optimization.contract.models import IntegrityMetadata
from autocontext.harness_optimization.leakage import AccessRecord, audit_leakage
from autocontext.harness_optimization.leakage_gate import evaluate_leakage_gate, leakage_gate_active_for
from autocontext.loop.stage_types import GenerationContext

if TYPE_CHECKING:
    from autocontext.loop.events import EventStreamEmitter

LEAKAGE_CHANNEL = "leakage"
_INTEGRITY_METADATA_KEY = "__integrity_metadata__"
_ACCESS_RECORDS_KEY = "__access_records__"


def stage_leakage(
    ctx: GenerationContext,
    *,
    events: EventStreamEmitter | None = None,
) -> GenerationContext:
    """Run the opt-in leakage gate when active; otherwise a byte-unchanged no-op."""

    if not leakage_gate_active_for(ctx.settings, ctx.scenario_name):
        return ctx

    strategy = ctx.current_strategy if isinstance(ctx.current_strategy, dict) else {}
    meta = strategy.get(_INTEGRITY_METADATA_KEY)
    if not isinstance(meta, dict):
        if events is not None:
            events.emit(
                "leakage_skipped",
                {"scenario": ctx.scenario_name, "reason": "no integrity metadata"},
                channel=LEAKAGE_CHANNEL,
            )
        return ctx

    records = strategy.get(_ACCESS_RECORDS_KEY, [])
    meta_model = IntegrityMetadata.model_validate(meta)
    access_records = [AccessRecord(**r) for r in records]
    audit = audit_leakage(meta_model, access_records)
    decision = evaluate_leakage_gate(audit, meta_model.mode, meta_model.prompt_provenance or "")

    if events is not None:
        events.emit(
            "leakage_clean" if decision.advance else "leakage_blocked",
            {
                "scenario": ctx.scenario_name,
                "status": audit.status,
                "advance": decision.advance,
                "non_promotion_grade": decision.non_promotion_grade,
                "rationale": decision.rationale,
            },
            channel=LEAKAGE_CHANNEL,
        )

    ctx.exploration_metadata["leakage_gate"] = {
        "status": audit.status,
        "advance": decision.advance,
        "non_promotion_grade": decision.non_promotion_grade,
        "rationale": decision.rationale,
    }
    if not decision.advance:
        ctx.gate_decision_history.append("leakage_blocked")

    return ctx
