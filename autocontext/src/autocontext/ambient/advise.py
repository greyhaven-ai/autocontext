"""the advise stage: heuristic charter proposals from observed trace quality.

Deliberately LLM-free (matching the hermes advisor's offline posture): the
rules are deterministic aggregates over recent traces. Advise only ever
EMITS proposals; applying one to the charter is a control-surface action
(autoctx ambient approve) at every autonomy level.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from pydantic import ValidationError

from autocontext.ambient.charter import Charter, CharterTarget
from autocontext.ambient.eligibility import split_role_selector
from autocontext.ambient.proposals import CharterProposal, ProposalStore
from autocontext.ambient.stage import StageContext, StageResult
from autocontext.ambient.trace_store import TraceStore


@dataclass(slots=True)
class _ScenarioStats:
    count: int = 0
    score_total: float = 0.0

    @property
    def mean_score(self) -> float:
        return self.score_total / self.count if self.count else 0.0


def _covered_scenarios(charter: Charter, store: ProposalStore) -> tuple[bool, set[str]]:
    """Returns (role_coverage, covered_selectors).

    An unscoped competitor role target trains on every scenario its role
    appears in, so it covers everything; task-family targets, competitor role
    targets bound to a single scenario (competitor@grid_ctf), and pending
    add_target proposals cover their specific selector.
    """
    # only an unscoped competitor role target covers all scenarios: the
    # advisor's signal is competitor-only, so any other role target (e.g.
    # analyst) trains on a different slice and must not permanently suppress
    # scenario proposals. a competitor binding scoped to one scenario
    # (competitor@grid_ctf) covers only that scenario, not the rest.
    role_coverage = False
    covered = {target.selector for target in charter.targets if target.kind == "task_family"}
    for target in charter.targets:
        if target.kind != "role":
            continue
        role, scenario = split_role_selector(target.selector)
        if role != "competitor":
            continue
        if scenario is None:
            role_coverage = True
        else:
            covered.add(scenario)
    # only pending proposals count as coverage: a rejected proposal's scenario is
    # deliberately eligible to be re-proposed on a later scan (rejection is a
    # decision about that proposal, not a permanent suppression of the scenario).
    for proposal in store.pending():
        selector = proposal.payload.get("selector")
        if proposal.kind == "add_target" and isinstance(selector, str):
            covered.add(selector)
    return role_coverage, covered


@dataclass(slots=True)
class AdviseStage:
    name: str
    trace_store: TraceStore
    scan_limit: int = 2000
    min_traces: int = 50
    min_mean_score: float = 0.5

    def run_once(self, ctx: StageContext) -> StageResult:
        store = ctx.proposal_store
        if store is None:
            return StageResult()
        stats: dict[str, _ScenarioStats] = {}
        for trace in self.trace_store.recent(self.scan_limit):
            if trace.kind != "agent_output" or trace.produced_by != "frontier":
                continue
            payload = trace.payload
            if payload.get("role") != "competitor" or payload.get("status") != "completed":
                continue
            scenario = payload.get("scenario")
            if not isinstance(scenario, str) or not scenario:
                continue
            entry = stats.setdefault(scenario, _ScenarioStats())
            entry.count += 1
            best_score = payload.get("best_score")
            entry.score_total += float(best_score) if best_score is not None else 0.0
        role_coverage, covered = _covered_scenarios(ctx.charter, store)
        emitted = 0
        for scenario in sorted(stats):
            entry = stats[scenario]
            if entry.count < self.min_traces or entry.mean_score < self.min_mean_score:
                continue
            if role_coverage or scenario in covered:
                continue
            template = next(iter(ctx.charter.targets), None)
            if template is None:
                # a proposal must not invent a base model or eval suite; ask
                # the human to seed the first target instead
                ctx.emitter.emit(
                    "advise_no_template",
                    {"scenario": scenario, "traces": entry.count, "mean_score": entry.mean_score},
                    channel="ambient",
                )
                continue
            # scenario names flow in from trace payloads and are not slug-validated
            # upstream, so a name the charter would reject must be skipped here
            # rather than allowed to trip the stage breaker (three raised cycles
            # auto-pause the advise stage).
            try:
                target = CharterTarget(
                    name=f"{scenario}-auto",
                    kind="task_family",
                    selector=scenario,
                    base_model=template.base_model,
                    method="sft-distill",
                    min_dataset_records=template.min_dataset_records,
                    eval_suite=template.eval_suite,
                )
            except ValidationError as exc:
                ctx.emitter.emit(
                    "advise_invalid_scenario",
                    {"scenario": scenario, "error": str(exc)},
                    channel="ambient",
                )
                continue
            proposal = CharterProposal(
                proposal_id=f"add-target-{scenario}-{uuid4().hex[:8]}",
                kind="add_target",
                payload=target.model_dump(mode="json"),
                rationale=(
                    f"{entry.count} eligible frontier traces for scenario {scenario} "
                    f"at mean score {entry.mean_score:.2f}; propose an sft-distill target"
                ),
            )
            store.append(proposal)
            ctx.emitter.emit(
                "advise_proposal_emitted",
                {
                    "proposal_id": proposal.proposal_id,
                    "selector": scenario,
                    "traces": entry.count,
                    "mean_score": entry.mean_score,
                },
                channel="ambient",
            )
            emitted += 1
        return StageResult(processed=emitted)
