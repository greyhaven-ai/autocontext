"""Tests for Phase 2: Retry Learning with Failure Context.

These tests verify that when backpressure triggers 'retry', the system:
- Varies tournament seeds across attempts
- Re-invokes the competitor with failure context
- Uses the revised strategy in subsequent tournament runs
- Preserves other agent outputs (analyst/coach/architect)
- Respects max_retries=0 by not retrying
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autocontext.agents.llm_client import DeterministicDevClient, LanguageModelClient, ModelResponse
from autocontext.backpressure.retry_context import RetryContext
from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner


class PromptCapturingClient(LanguageModelClient):
    """Wraps DeterministicDevClient to capture all prompts sent to generate()."""

    def __init__(self) -> None:
        self._inner = DeterministicDevClient()
        self.captured_prompts: list[str] = []

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        self.captured_prompts.append(prompt)
        return self._inner.generate(
            model=model, prompt=prompt, max_tokens=max_tokens, temperature=temperature,
        )

    def generate_multiturn(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        return self._inner.generate_multiturn(
            model=model, system=system, messages=messages,
            max_tokens=max_tokens, temperature=temperature,
        )

    def reset_rlm_turns(self) -> None:
        self._inner.reset_rlm_turns()


def _make_settings(tmp_path: Path, **overrides: Any) -> AppSettings:
    defaults: dict[str, Any] = {
        "db_path": tmp_path / "runs" / "autocontext.sqlite3",
        "runs_root": tmp_path / "runs",
        "knowledge_root": tmp_path / "knowledge",
        "skills_root": tmp_path / "skills",
        "event_stream_path": tmp_path / "runs" / "events.ndjson",
        "seed_base": 2000,
        "agent_provider": "deterministic",
        "matches_per_generation": 2,
        "retry_backoff_seconds": 0.0,
    }
    defaults.update(overrides)
    return AppSettings(**defaults)


def _make_runner(settings: AppSettings) -> GenerationRunner:
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)
    return runner


# ---- Test 1: RetryContext dataclass ----

def test_retry_context_dataclass() -> None:
    ctx = RetryContext(
        attempt=2,
        previous_score=0.45,
        best_score_needed=0.5,
        gate_threshold=0.005,
        previous_strategy={"aggression": 0.5, "defense": 0.5, "path_bias": 0.5},
        gate_reason="insufficient improvement; retry permitted",
    )
    assert ctx.attempt == 2
    assert ctx.previous_score == 0.45
    assert ctx.best_score_needed == 0.5
    assert ctx.gate_threshold == 0.005
    assert ctx.previous_strategy == {"aggression": 0.5, "defense": 0.5, "path_bias": 0.5}
    assert ctx.gate_reason == "insufficient improvement; retry permitted"

    # Verify frozen
    try:
        ctx.attempt = 3  # type: ignore[misc]
        raise AssertionError("Should have raised FrozenInstanceError")
    except AttributeError:
        pass

    # Verify slots
    assert hasattr(ctx, "__slots__")


# ---- Test 2: Retry varies seeds ----

def test_retry_varies_seeds(tmp_path: Path) -> None:
    """When retry is triggered, tournament seeds must differ from the first attempt."""
    # Use a very high min_delta so gen 2 always retries (gen 1 advances from 0.0)
    settings = _make_settings(
        tmp_path,
        backpressure_min_delta=0.99,
        max_retries=1,
    )
    runner = _make_runner(settings)

    # Capture seed values passed to supervisor.run (via ExecutionInput payloads)
    seeds_seen: list[int] = []
    original_run = runner.executor.run

    def capturing_supervisor_run(scenario: Any, payload: Any) -> Any:
        seeds_seen.append(payload.seed)
        return original_run(scenario, payload)

    runner.executor.run = capturing_supervisor_run  # type: ignore[assignment]

    runner.run(scenario_name="grid_ctf", generations=2, run_id="seed_test")

    # Gen 1 always runs once (advances from 0.0). Gen 2 should have at least 2 attempts
    # (original + 1 retry) with different seed values.
    # Each tournament attempt runs matches_per_generation=2 matches.
    # So gen 1 = 2 seeds, gen 2 first attempt = 2 seeds, gen 2 retry = 2 seeds => >= 6 total
    assert len(seeds_seen) >= 6, f"Expected at least 6 match seeds, got {len(seeds_seen)}: {seeds_seen}"
    # The gen 2 retry seeds should differ from the gen 2 initial seeds
    gen2_initial_base = settings.seed_base + (2 * 100)  # attempt=0
    gen2_retry_base = settings.seed_base + (2 * 100) + 10  # attempt=1
    assert gen2_initial_base in seeds_seen, f"Gen 2 initial seed {gen2_initial_base} not found in {seeds_seen}"
    assert gen2_retry_base in seeds_seen, f"Gen 2 retry seed {gen2_retry_base} not found in {seeds_seen}"


# ---- Test 3: Retry re-invokes competitor with RETRY ATTEMPT prompt ----

def test_retry_reinvokes_competitor(tmp_path: Path) -> None:
    """On retry, the competitor should be re-invoked with a prompt containing RETRY ATTEMPT."""
    settings = _make_settings(
        tmp_path,
        backpressure_min_delta=0.99,
        max_retries=1,
    )
    capturing_client = PromptCapturingClient()
    runner = _make_runner(settings)
    # Replace the orchestrator's client and all runtime clients
    runner.agents.client = capturing_client
    runner.agents.competitor.runtime.client = capturing_client
    runner.agents.translator.runtime.client = capturing_client

    runner.run(scenario_name="grid_ctf", generations=2, run_id="retry_prompt_test")

    # Find prompts containing "RETRY ATTEMPT"
    retry_prompts = [p for p in capturing_client.captured_prompts if "RETRY ATTEMPT" in p]
    assert len(retry_prompts) >= 1, (
        f"Expected at least one RETRY ATTEMPT prompt, found {len(retry_prompts)}. "
        f"Total prompts captured: {len(capturing_client.captured_prompts)}"
    )
    # The retry prompt should mention the previous score
    assert any("previous strategy scored" in p.lower() for p in retry_prompts), (
        "Retry prompt should mention the previous score"
    )


# ---- Test 4: Retry uses revised strategy ----

def test_retry_uses_revised_strategy(tmp_path: Path) -> None:
    """The strategy dict used in the retry tournament should differ from the first attempt within the same generation."""
    settings = _make_settings(
        tmp_path,
        backpressure_min_delta=0.99,
        max_retries=1,
    )
    runner = _make_runner(settings)

    calls: list[dict[str, Any]] = []
    original_run = runner.executor.run

    def capturing_supervisor_run(scenario: Any, payload: Any) -> Any:
        calls.append({"strategy": dict(payload.strategy), "seed": payload.seed})
        return original_run(scenario, payload)

    runner.executor.run = capturing_supervisor_run  # type: ignore[assignment]

    runner.run(scenario_name="grid_ctf", generations=2, run_id="strategy_test")

    # With min_delta=0.99, max_retries=1, and matches_per_generation=2:
    # Gen 1 = 1 attempt x 2 matches = 2 calls (advances from 0.0)
    # Gen 2 = 2 attempts x 2 matches = 4 calls (initial + retry)
    # Total >= 6
    assert len(calls) >= 6, (
        f"Expected at least 6 supervisor.run calls (2 gens, gen2 retried, 2 matches each), got {len(calls)}"
    )

    # Group by seed range to identify generation boundaries.
    # Gen 1 seeds start at 2000 + 100 = 2100, gen 2 at 2000 + 200 = 2200.
    gen2_calls = [c for c in calls if c["seed"] >= settings.seed_base + 200]
    assert len(gen2_calls) >= 4, f"Expected at least 4 gen-2 match calls, got {len(gen2_calls)}"

    # Gen 2 initial attempt seeds: 2200, 2201; retry attempt seeds: 2210, 2211
    gen2_initial_strategies = {
        frozenset(c["strategy"].items()) for c in gen2_calls if c["seed"] < settings.seed_base + 200 + 10
    }
    gen2_retry_strategies = {
        frozenset(c["strategy"].items()) for c in gen2_calls if c["seed"] >= settings.seed_base + 200 + 10
    }
    assert len(gen2_initial_strategies) >= 1
    assert len(gen2_retry_strategies) >= 1
    assert gen2_initial_strategies != gen2_retry_strategies, (
        f"Gen 2 retry strategy should differ from initial within same generation: "
        f"{gen2_initial_strategies} vs {gen2_retry_strategies}"
    )


# ---- Test 5: Retry preserves other agent outputs ----

def test_retry_preserves_other_agent_outputs(tmp_path: Path) -> None:
    """After retry, analyst/coach/architect outputs in DB should be from original invocation (not re-run)."""
    settings = _make_settings(
        tmp_path,
        backpressure_min_delta=0.99,
        max_retries=1,
    )
    runner = _make_runner(settings)

    runner.run(scenario_name="grid_ctf", generations=2, run_id="preserve_test")

    # Query agent_outputs for gen 2
    with runner.sqlite.connect() as conn:
        rows = conn.execute(
            "SELECT role, content FROM agent_outputs WHERE run_id = ? AND generation_index = 2",
            ("preserve_test",),
        ).fetchall()

    outputs_by_role = {row["role"]: row["content"] for row in rows}
    # Analyst, coach, architect should each have exactly one output (the original, not re-run)
    assert "analyst" in outputs_by_role
    assert "coach" in outputs_by_role
    assert "architect" in outputs_by_role

    # Each should have non-empty content from original invocation
    assert len(outputs_by_role["analyst"]) > 0
    assert len(outputs_by_role["coach"]) > 0
    assert len(outputs_by_role["architect"]) > 0

    # The analyst content should still be the original analysis
    assert "Findings" in outputs_by_role["analyst"] or "findings" in outputs_by_role["analyst"].lower()


# ---- Test 6: No retry when max_retries=0 ----

def test_no_retry_when_max_retries_zero(tmp_path: Path) -> None:
    """With max_retries=0, the competitor is called exactly once per generation."""
    settings = _make_settings(
        tmp_path,
        backpressure_min_delta=0.99,
        max_retries=0,
    )
    capturing_client = PromptCapturingClient()
    runner = _make_runner(settings)
    runner.agents.client = capturing_client
    runner.agents.competitor.runtime.client = capturing_client
    runner.agents.translator.runtime.client = capturing_client

    runner.run(scenario_name="grid_ctf", generations=2, run_id="no_retry_test")

    # Count competitor prompts (those containing "Describe your strategy")
    competitor_prompts = [p for p in capturing_client.captured_prompts if "describe your strategy" in p.lower()]
    # Should be exactly 2: one for gen 1, one for gen 2. No retries.
    assert len(competitor_prompts) == 2, (
        f"Expected exactly 2 competitor prompts (no retries), got {len(competitor_prompts)}"
    )
    # None should contain RETRY ATTEMPT
    retry_prompts = [p for p in capturing_client.captured_prompts if "RETRY ATTEMPT" in p]
    assert len(retry_prompts) == 0, (
        f"Expected no RETRY ATTEMPT prompts with max_retries=0, got {len(retry_prompts)}"
    )
