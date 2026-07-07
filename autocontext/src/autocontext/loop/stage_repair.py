"""Opt-in deterministic repair stage: runs the RepairGate before validation.

This is the pre-scoring seam for the AC-878 repair gates. It sits immediately
before :func:`stage_staged_validation` so any structural recovery happens before
a candidate is scored. The stage is caller-gated: it early-returns unless
:func:`repair_gate_active_for` says the gate is active for this scenario, so with
the default flags OFF the generation path is byte-unchanged (this stage is a
no-op that touches nothing on ``ctx`` and emits no events).

When active, it builds a :class:`RepairContext` from the recorded state present
on the :class:`GenerationContext` and runs the gate, which emits one
``repair_applied`` / ``repair_skipped`` event per repair on the ``repair``
channel. The stage is deterministic and never fabricates task content: it only
runs the pure repairs and records their structural decisions.

The lone recorded-state input wired today is a raw tool-call JSON string the
competitor stage may attach under ``ctx.current_strategy["__tool_call_json__"]``;
when present and structurally repairable, the repaired string is recorded back
onto the same key so downstream consumers see valid JSON. Richer inputs
(artifact-landing relocation, finish-claim validation) and the runtime
parse-seam wiring are follow-ups tracked with the deferred artifact-reassembly
work in ``docs/harness-optimization-protocol.md``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from autocontext.harness_optimization.repair_gate import (
    RepairContext,
    RepairGate,
    repair_gate_active_for,
)
from autocontext.loop.stage_types import GenerationContext

if TYPE_CHECKING:
    from autocontext.loop.events import EventStreamEmitter

logger = logging.getLogger(__name__)

_RAW_TOOL_CALL_KEY = "__tool_call_json__"


def _repair_context_from(ctx: GenerationContext) -> RepairContext:
    """Build a RepairContext from the recorded state available on ``ctx``.

    Only structural inputs the pipeline actually records are mapped; everything
    else stays absent so the corresponding repair returns ``not_applicable``.
    """

    raw_tool_call = ctx.current_strategy.get(_RAW_TOOL_CALL_KEY) if isinstance(ctx.current_strategy, dict) else None
    return RepairContext(tool_call_json=raw_tool_call if isinstance(raw_tool_call, str) else None)


def stage_repair(
    ctx: GenerationContext,
    *,
    events: EventStreamEmitter,
) -> GenerationContext:
    """Run the opt-in RepairGate when active; otherwise a byte-unchanged no-op."""

    if not repair_gate_active_for(ctx.settings, ctx.scenario_name):
        return ctx

    context = _repair_context_from(ctx)
    gate = RepairGate(emitter=events)
    gate.run(ctx.scenario_name, context)

    # Record a structurally repaired tool-call back onto the strategy so the
    # downstream stages see valid JSON. This only happens under the opt-in flag.
    if context.repaired_tool_call_json is not None and isinstance(ctx.current_strategy, dict):
        ctx.current_strategy[_RAW_TOOL_CALL_KEY] = context.repaired_tool_call_json

    return ctx
