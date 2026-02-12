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

from mts.agents.llm_client import DeterministicDevClient, LanguageModelClient, ModelResponse
from mts.backpressure.retry_context import RetryContext
from mts.config import AppSettings
from mts.loop import GenerationRunner


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
        "db_path": tmp_path / "runs" / "mts.sqlite3",
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

    # Capture seed_base values passed to tournament.run
    seed_bases_seen: list[int] = []
    original_run = runner.tournament.run

    def capturing_tournament_run(**kwargs: Any) -> Any:
        seed_bases_seen.append(kwargs["seed_base"])
        return original_run(**kwargs)

    runner.tournament.run = capturing_tournament_run  # type: ignore[assignment]

    runner.run(scenario_name="grid_ctf", generations=2, run_id="seed_test")

    # Gen 1 always runs once (advances from 0.0). Gen 2 should have at least 2 attempts
    # (original + 1 retry) with different seed_base values.
    # There should be multiple tournament runs for gen 2, and the retry should have a different seed
    assert len(seed_bases_seen) >= 3, f"Expected at least 3 tournament runs, got {len(seed_bases_seen)}: {seed_bases_seen}"
    # The gen 2 retry seed should differ from the gen 2 initial seed
    gen2_initial = settings.seed_base + (2 * 100)  # attempt=0
    gen2_retry = settings.seed_base + (2 * 100) + 10  # attempt=1
    assert gen2_initial in seed_bases_seen, f"Gen 2 initial seed {gen2_initial} not found in {seed_bases_seen}"
    assert gen2_retry in seed_bases_seen, f"Gen 2 retry seed {gen2_retry} not found in {seed_bases_seen}"


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
    original_run = runner.tournament.run

    def capturing_tournament_run(**kwargs: Any) -> Any:
        calls.append({"strategy": dict(kwargs["strategy"]), "seed_base": kwargs["seed_base"]})
        return original_run(**kwargs)

    runner.tournament.run = capturing_tournament_run  # type: ignore[assignment]

    runner.run(scenario_name="grid_ctf", generations=2, run_id="strategy_test")

    # With min_delta=0.99 and max_retries=1, each generation gets 2 tournament runs
    # (initial + 1 retry), so we expect 4 total.
    assert len(calls) >= 4, (
        f"Expected at least 4 tournament runs (2 gens x 2 attempts), got {len(calls)}"
    )

    # Group by seed_base prefix to identify generation boundaries.
    # Gen 1 seeds start at 2000 + 100 = 2100, gen 2 at 2000 + 200 = 2200.
    gen2_calls = [c for c in calls if c["seed_base"] >= settings.seed_base + 200]
    assert len(gen2_calls) >= 2, f"Expected at least 2 gen-2 tournament runs, got {len(gen2_calls)}"

    gen2_initial_strategy = gen2_calls[0]["strategy"]
    gen2_retry_strategy = gen2_calls[1]["strategy"]
    assert gen2_initial_strategy != gen2_retry_strategy, (
        f"Gen 2 retry strategy should differ from initial within same generation: "
        f"{gen2_initial_strategy} vs {gen2_retry_strategy}"
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
