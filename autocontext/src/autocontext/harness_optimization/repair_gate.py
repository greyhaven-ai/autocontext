"""Opt-in RepairGate: run deterministic repairs and emit trace events (AC-878).

The gate is a thin, deterministic orchestrator over the pure repairs in
:mod:`autocontext.harness_optimization.repairs`. It does NOT decide whether it is
active: that is the caller's job via :func:`repair_gate_active_for`. When the
caller determines the gate is active for a scenario, it constructs a
:class:`RepairGate` and calls :meth:`RepairGate.run`, which invokes each enabled
repair over the handed context, emits exactly one ``repair_applied`` /
``repair_skipped`` trace event per repair, and returns the collected
:class:`RepairResult` list.

The gate MAY apply the decision a pure repair returns (record the repaired
tool-call json string, record the relocation target) by writing it back onto the
context, but it never introduces or alters task content: every applied decision
is a structural one the pure repair already made. ``repair_applied`` is emitted
when a repair's status is ``applied``; ``repair_skipped`` covers both ``skipped``
and ``not_applicable`` (nothing was changed). Events go on the ``repair``
channel and carry ``result.model_dump(mode="json")`` verbatim, so every emitted
payload validates against the RepairResult schema.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from autocontext.config.settings import AppSettings
from autocontext.control_plane.contract_probes._base import ArtifactContractProbeInputs
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.harness_optimization.contract.models import Parity, RepairResult
from autocontext.harness_optimization.repairs import (
    finish_guard,
    repair_artifact_landing,
    repair_tool_call_json,
)

REPAIR_CHANNEL = "repair"


def repair_gate_active_for(settings: AppSettings, scenario_name: str) -> bool:
    """True iff the gate is globally enabled AND the scenario is allowlisted.

    The allowlist is the comma-separated ``harness_repair_gate_scenarios``
    setting; an empty allowlist means no scenario is active even when the global
    flag is on. This is the sole opt-in decision: callers check it and only build
    and run a :class:`RepairGate` when it returns True.
    """

    allowlist = {s.strip() for s in settings.harness_repair_gate_scenarios.split(",") if s.strip()}
    return settings.harness_repair_gates_enabled and scenario_name in allowlist


@dataclass
class RepairContext:
    """Recorded state handed to the gate, one field group per enabled repair.

    Every field is optional: a repair whose input is absent returns a
    ``not_applicable`` result. The gate writes applied decisions back onto the
    two output fields (``repaired_tool_call_json``, ``relocation_target``).
    """

    tool_call_json: str | None = None
    repaired_tool_call_json: str | None = None
    artifact_expected: ArtifactContractProbeInputs | None = None
    artifact_produced: dict[str, str] = field(default_factory=dict)
    relocation_target: str | None = None
    finish_claimed_done: bool | None = None
    finish_completion_ok: bool = True
    finish_reason_if_not: str = ""


RepairStep = Callable[[RepairContext], RepairResult]


def _absent_result(repair_name: str, reason: str) -> RepairResult:
    """A ``not_applicable`` result for a repair whose input is absent."""

    return RepairResult(
        schema_version=1,
        repair_name=repair_name,
        status="not_applicable",
        reason=reason,
        target="",
        before={"present": False},
        after={"present": False},
        # These absent-input repairs (tool_call_json, artifact_landing, finish_guard) are implemented
        # in BOTH languages, so their parity is implemented/implemented, the same as their applied and
        # not_applicable results. Stamping the other language "pending" made a normal skipped event
        # look like a parity gap in audit output. loop_guard (Python-only) never uses this helper.
        parity=Parity(python="implemented", typescript="implemented", schema_hash=""),
    )


def repair_tool_call_json_step(ctx: RepairContext) -> RepairResult:
    """Run the tool-call json repair; record the repaired string when applied."""

    if ctx.tool_call_json is None:
        return _absent_result("tool_call_json", "no tool-call json in context")
    value, result = repair_tool_call_json(ctx.tool_call_json)
    if result.status == "applied" and value is not None:
        ctx.repaired_tool_call_json = value
    return result


def repair_artifact_landing_step(ctx: RepairContext) -> RepairResult:
    """Run the artifact-landing repair; record the relocation target when applied."""

    if ctx.artifact_expected is None:
        return _absent_result("artifact_landing", "no expected artifact contract in context")
    target, result = repair_artifact_landing(expected=ctx.artifact_expected, produced=ctx.artifact_produced)
    if result.status == "applied" and target is not None:
        ctx.relocation_target = target
    return result


def finish_guard_step(ctx: RepairContext) -> RepairResult:
    """Run the finish guard when a completion claim is present in the context."""

    if ctx.finish_claimed_done is None:
        return _absent_result("finish_guard", "no finish claim in context")
    return finish_guard(
        claimed_done=ctx.finish_claimed_done,
        completion_ok=ctx.finish_completion_ok,
        reason_if_not=ctx.finish_reason_if_not,
    )


# loop_guard is intentionally omitted from the default set: it has no TypeScript
# mirror yet, and the default set is kept identical across languages.
DEFAULT_REPAIRS: tuple[RepairStep, ...] = (
    repair_tool_call_json_step,
    repair_artifact_landing_step,
    finish_guard_step,
)


@dataclass
class RepairGate:
    """Thin orchestrator: run each enabled repair, emit one event per result.

    ``run`` does NOT check whether the gate is active; the caller gates via
    :func:`repair_gate_active_for` and only constructs and runs the gate when
    active.
    """

    emitter: EventStreamEmitter
    repairs: Sequence[RepairStep] = DEFAULT_REPAIRS
    channel: str = REPAIR_CHANNEL

    def run(self, scenario_name: str, context: RepairContext) -> list[RepairResult]:
        """Invoke each enabled repair over ``context``, emitting an event each.

        The emitted payload is ``{"scenario": scenario_name, "result": <dump>}``:
        the RepairResult dump stays a self-contained, schema-valid object under
        ``result``, and the scenario rides alongside as a sibling so consumers
        can attribute the repair without polluting the RepairResult schema.
        """

        results: list[RepairResult] = []
        for repair in self.repairs:
            result = repair(context)
            event = "repair_applied" if result.status == "applied" else "repair_skipped"
            payload = {"scenario": scenario_name, "result": result.model_dump(mode="json")}
            self.emitter.emit(event, payload, channel=self.channel)
            results.append(result)
        return results
