"""Pre-registered data generation for the judge pre-screen (design Phase 0).

Runs a committed task corpus through the improvement loop with the judge ledger on, under hard caps on provider calls
and provider-reported cost. The output directory holds the protocol copy, its sha256, progress and a summary: counts
only, never prompt or output text.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.prescreen.ledger import JudgeLedger
from autocontext.prescreen.rounds import LedgerRound, eligible_rounds
from autocontext.providers.base import CompletionResult, LLMProvider, OutputSchema
from autocontext.scenarios.agent_task import AgentTaskInterface
from autocontext.storage.sqlite_store import SQLiteStore

DATAGEN_FAMILY = "datagen"
_REQUIRED = (
    "protocol_version",
    "model",
    "repetitions",
    "max_rounds",
    "quality_threshold",
    "judge_samples",
    "max_provider_calls",
    "max_reported_cost_usd",
    "target_eligible_rounds",
    "corpus",
)


@dataclass(frozen=True)
class DatagenTask:
    task_id: str
    category: str
    prompt: str
    rubric: str


@dataclass(frozen=True)
class DatagenProtocol:
    protocol_version: str
    model: str
    repetitions: int
    max_rounds: int
    quality_threshold: float
    judge_samples: int
    max_provider_calls: int
    max_reported_cost_usd: float
    target_eligible_rounds: int
    corpus: tuple[DatagenTask, ...]


def load_protocol(path: Path) -> tuple[DatagenProtocol, bytes]:
    """Parse and validate a committed protocol; the raw bytes are returned so a run can record their sha256."""
    raw = path.read_bytes()
    spec = json.loads(raw)
    missing = [key for key in _REQUIRED if key not in spec]
    if missing:
        raise ValueError(f"missing protocol keys: {missing}")
    for key in ("repetitions", "judge_samples", "max_provider_calls", "target_eligible_rounds"):
        if int(spec[key]) < 1:
            raise ValueError(f"{key} must be at least 1")
    if int(spec["max_rounds"]) < 2:
        raise ValueError("max_rounds must be at least 2, or no round can ever be eligible")
    if not 0 < float(spec["quality_threshold"]) <= 1 or float(spec["max_reported_cost_usd"]) <= 0:
        raise ValueError("quality_threshold must be in (0, 1] and max_reported_cost_usd positive")
    tasks = [
        DatagenTask(str(t["task_id"]), str(t["category"]), str(t["prompt"]), str(t["rubric"]))
        for t in (
            json.loads(line) for line in (path.parent / spec["corpus"]).read_text(encoding="utf-8").splitlines() if line.strip()
        )
    ]
    if not tasks:
        raise ValueError("empty corpus")
    if len({t.task_id for t in tasks}) != len(tasks):
        raise ValueError("duplicate task_id in corpus")
    protocol = DatagenProtocol(
        protocol_version=str(spec["protocol_version"]),
        model=str(spec["model"]),
        repetitions=int(spec["repetitions"]),
        max_rounds=int(spec["max_rounds"]),
        quality_threshold=float(spec["quality_threshold"]),
        judge_samples=int(spec["judge_samples"]),
        max_provider_calls=int(spec["max_provider_calls"]),
        max_reported_cost_usd=float(spec["max_reported_cost_usd"]),
        target_eligible_rounds=int(spec["target_eligible_rounds"]),
        corpus=tuple(tasks),
    )
    return protocol, raw


class CallBudgetExhausted(RuntimeError):
    pass


class _TargetReached(Exception):
    """Internal: the eligible-round target is met, so the run stops early."""


class CountingProvider(LLMProvider):
    """Forwards to a real provider and refuses any call past the pre-registered call or reported-cost cap.

    It forwards `name` and the capability flags, so the judge's serving spec, and so its judge identity, is unchanged.
    """

    def __init__(self, inner: LLMProvider, *, max_calls: int, max_cost_usd: float) -> None:
        self.inner = inner
        self.max_calls = max_calls
        self.max_cost_usd = max_cost_usd
        self.calls = 0
        self.reported_cost_usd = 0.0

    def _spend(self) -> None:
        if self.calls >= self.max_calls:
            raise CallBudgetExhausted(f"pre-registered cap of {self.max_calls} provider calls reached")
        if self.reported_cost_usd >= self.max_cost_usd:
            raise CallBudgetExhausted(f"pre-registered cost cap of ${self.max_cost_usd:.2f} reached")
        self.calls += 1

    def _account(self, result: CompletionResult) -> CompletionResult:
        if result.cost_usd is not None:
            self.reported_cost_usd += result.cost_usd
        return result

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        output_schema: OutputSchema | None = None,
    ) -> CompletionResult:
        self._spend()
        return self._account(
            self.inner.complete(
                system_prompt,
                user_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                output_schema=output_schema,
            )
        )

    def complete_with_thinking(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        output_schema: OutputSchema | None = None,
        reasoning_effort: str = "medium",
        max_tool_turns: int = 8,
    ) -> CompletionResult:
        self._spend()
        return self._account(
            self.inner.complete_with_thinking(
                system_prompt,
                user_prompt,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                output_schema=output_schema,
                reasoning_effort=reasoning_effort,
                max_tool_turns=max_tool_turns,
            )
        )

    def default_model(self) -> str:
        return self.inner.default_model()

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def supports_thinking_stream(self) -> bool:
        return self.inner.supports_thinking_stream

    @property
    def supports_single_dispatch(self) -> bool:
        return self.inner.supports_single_dispatch

    @property
    def supports_thinking_output_schema(self) -> bool:
        return self.inner.supports_thinking_output_schema


TaskFactory = Callable[[DatagenTask, LLMProvider, DatagenProtocol], AgentTaskInterface]


def simple_task_factory(task: DatagenTask, provider: LLMProvider, protocol: DatagenProtocol) -> AgentTaskInterface:
    from autocontext.execution.task_runner import SimpleAgentTask

    return SimpleAgentTask(task.prompt, task.rubric, provider, model=protocol.model, judge_samples=protocol.judge_samples)


@dataclass(frozen=True)
class DatagenSummary:
    protocol_version: str
    run_prefix: str
    loops: int
    provider_calls: int
    reported_cost_usd: float
    judged_rounds: int
    eligible_rounds: int
    judge_identities: int
    stop_reason: str


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_rounds(store: SQLiteStore, run_prefix: str) -> list[LedgerRound]:
    rows = store.list_judge_ledger_rows(scenario_family=DATAGEN_FAMILY)
    return [LedgerRound.from_row(r) for r in rows if (r["run_id"] or "").startswith(run_prefix)]


def count_rounds(store: SQLiteStore, run_prefix: str) -> tuple[int, int]:
    """(judged rounds, eligible rounds in this run's largest (judge identity, datagen) cell) recorded so far.

    Phase 1 is certified per (judge identity, scenario family), so only one cell's eligible rounds count toward the
    target; rounds under different judges never add up.
    """
    rounds = _run_rounds(store, run_prefix)
    cells = Counter(e.round.judge_identity for e in eligible_rounds(rounds))
    return len(rounds), max(cells.values(), default=0)


def run_datagen(
    protocol: DatagenProtocol,
    raw_protocol: bytes,
    *,
    out_dir: Path,
    store: SQLiteStore,
    provider: LLMProvider,
    task_factory: TaskFactory = simple_task_factory,
    log: Callable[[str], None] = print,
) -> DatagenSummary:
    """Run the corpus repetitions until the eligible target, a cap, or the end of the corpus."""
    out_dir.mkdir(parents=True, exist_ok=False)
    (out_dir / "protocol.json").write_bytes(raw_protocol)
    run_prefix = f"datagen_{uuid.uuid4().hex[:10]}"
    _write(
        out_dir / "identity.json",
        {
            "protocol_version": protocol.protocol_version,
            "protocol_sha256": hashlib.sha256(raw_protocol).hexdigest(),
            "run_prefix": run_prefix,
            "started_at": datetime.now(UTC).isoformat(),
        },
    )
    capped = CountingProvider(provider, max_calls=protocol.max_provider_calls, max_cost_usd=protocol.max_reported_cost_usd)
    loops, stop = 0, "corpus_exhausted"
    try:
        for repetition in range(protocol.repetitions):
            for task in protocol.corpus:
                judged, eligible = count_rounds(store, run_prefix)
                if eligible >= protocol.target_eligible_rounds:
                    stop = "target_reached"
                    raise _TargetReached
                agent_task = task_factory(task, capped, protocol)
                initial = capped.complete(
                    system_prompt="Complete the task precisely.", user_prompt=task.prompt, model=protocol.model
                ).text
                ledger = JudgeLedger(
                    store,
                    run_id=f"{run_prefix}_r{repetition}",
                    scenario_name=f"{task.category}/{task.task_id}",
                    scenario_family=DATAGEN_FAMILY,
                )
                ImprovementLoop(
                    agent_task, max_rounds=protocol.max_rounds, quality_threshold=protocol.quality_threshold, judge_ledger=ledger
                ).run(initial_output=initial, state={})
                loops += 1
                _write(
                    out_dir / "progress.json",
                    {"loops": loops, "provider_calls": capped.calls, "reported_cost_usd": capped.reported_cost_usd},
                )
                log(f"datagen: loop {loops} ({task.category}/{task.task_id}, repetition {repetition}) calls={capped.calls}")
    except CallBudgetExhausted:
        stop = "call_budget_exhausted"
    except _TargetReached:
        pass
    judged, eligible = count_rounds(store, run_prefix)
    summary = DatagenSummary(
        protocol_version=protocol.protocol_version,
        run_prefix=run_prefix,
        loops=loops,
        provider_calls=capped.calls,
        reported_cost_usd=round(capped.reported_cost_usd, 6),
        judged_rounds=judged,
        eligible_rounds=eligible,
        judge_identities=len({r.judge_identity for r in _run_rounds(store, run_prefix) if r.judge_identity is not None}),
        stop_reason=stop,
    )
    _write(out_dir / "summary.json", asdict(summary))
    return summary
