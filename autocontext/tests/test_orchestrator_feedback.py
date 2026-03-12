"""Tests for inter-agent feedback: analyst output enriches coach prompt."""
from __future__ import annotations

from pathlib import Path

from autocontext.agents.llm_client import DeterministicDevClient, LanguageModelClient, ModelResponse
from autocontext.agents.orchestrator import AgentOrchestrator
from autocontext.config.settings import AppSettings
from autocontext.prompts.templates import PromptBundle


class PromptCapturingClient(LanguageModelClient):
    """Wraps DeterministicDevClient, recording (role, prompt) for each call."""

    def __init__(self) -> None:
        self._inner = DeterministicDevClient()
        self.calls: list[tuple[str, str]] = []

    def _detect_role(self, prompt: str) -> str:
        lower = prompt.lower()
        if "extract the strategy" in lower:
            return "translator"
        if "describe your strategy" in lower:
            return "competitor"
        if "analyze strengths/failures" in lower or "findings, root causes" in lower:
            return "analyst"
        if "playbook_start" in lower or "you are the playbook coach" in lower or "update the playbook" in lower:
            return "coach"
        return "architect"

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> ModelResponse:
        detected = self._detect_role(prompt)
        self.calls.append((detected, prompt))
        return self._inner.generate(model=model, prompt=prompt, max_tokens=max_tokens, temperature=temperature)

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
        combined = system + "\n\n" + "\n\n".join(m["content"] for m in messages if m["role"] == "user")
        detected = self._detect_role(combined)
        self.calls.append((detected, combined))
        return self._inner.generate_multiturn(
            model=model, system=system, messages=messages, max_tokens=max_tokens, temperature=temperature,
        )

    def reset_rlm_turns(self) -> None:
        self._inner.reset_rlm_turns()


def _make_prompt_bundle() -> PromptBundle:
    """Build a minimal PromptBundle matching what build_prompt_bundle produces."""
    base = (
        "Scenario rules:\nTest scenario\n\n"
        "Strategy interface:\n{\"aggression\": float, \"defense\": float, \"path_bias\": float}\n\n"
        "Evaluation criteria:\nWin rate\n\n"
        "Observation narrative:\nTest narrative\n\n"
        "Observation state:\n{}\n\n"
        "Constraints:\nNone\n\n"
        "Current playbook:\nNo playbook yet\n\n"
        "Available tools:\nNone\n\n"
        "Previous generation summary:\nNone\n"
    )
    return PromptBundle(
        competitor=base + "Describe your strategy reasoning and recommend specific parameter values.",
        analyst=base + "Analyze strengths/failures and return markdown with sections: "
        "Findings, Root Causes, Actionable Recommendations.",
        coach=base + (
            "You are the playbook coach. Produce TWO structured sections:\n\n"
            "1. A COMPLETE replacement playbook between markers.\n\n"
            "<!-- PLAYBOOK_START -->\n(Your consolidated playbook here)\n<!-- PLAYBOOK_END -->\n\n"
            "2. Operational lessons learned between markers.\n\n"
            "<!-- LESSONS_START -->\n(lessons)\n<!-- LESSONS_END -->"
        ),
        architect=base + "Propose infrastructure/tooling improvements.",
    )


def _make_settings() -> AppSettings:
    return AppSettings(agent_provider="deterministic")


def test_analyst_output_passed_to_coach_prompt() -> None:
    """Coach prompt must contain analyst findings from the same generation."""
    client = PromptCapturingClient()
    settings = _make_settings()
    orch = AgentOrchestrator(client=client, settings=settings)
    prompts = _make_prompt_bundle()

    orch.run_generation(prompts, generation_index=1)

    coach_calls = [(role, prompt) for role, prompt in client.calls if role == "coach"]
    assert len(coach_calls) == 1, f"Expected 1 coach call, got {len(coach_calls)}"
    coach_prompt = coach_calls[0][1]

    # The DeterministicDevClient analyst returns text containing "## Findings"
    assert "## Findings" in coach_prompt, "Coach prompt should contain analyst findings"
    assert "Analyst findings (this generation)" in coach_prompt or "analyst findings" in coach_prompt.lower(), (
        "Coach prompt should contain the analyst feedback marker"
    )


def test_architect_independent_of_analyst() -> None:
    """Architect prompt must NOT contain analyst output text."""
    client = PromptCapturingClient()
    settings = _make_settings()
    orch = AgentOrchestrator(client=client, settings=settings)
    prompts = _make_prompt_bundle()

    orch.run_generation(prompts, generation_index=1)

    architect_calls = [(role, prompt) for role, prompt in client.calls if role == "architect"]
    assert len(architect_calls) == 1, f"Expected 1 architect call, got {len(architect_calls)}"
    architect_prompt = architect_calls[0][1]

    # Analyst findings text should NOT appear in architect prompt
    assert "Analyst findings (this generation)" not in architect_prompt
    assert "Strategy balances offense/defense" not in architect_prompt


def test_feedback_flow_backward_compatible() -> None:
    """Full generation still produces all expected AgentOutputs fields."""
    client = DeterministicDevClient()
    settings = _make_settings()
    orch = AgentOrchestrator(client=client, settings=settings)
    prompts = _make_prompt_bundle()

    outputs = orch.run_generation(prompts, generation_index=1)

    assert isinstance(outputs.strategy, dict)
    assert outputs.analysis_markdown
    assert outputs.coach_markdown
    assert outputs.architect_markdown
    assert len(outputs.role_executions) == 5


def test_rlm_path_also_enriches_coach(tmp_path: Path) -> None:
    """When rlm_enabled=True, coach prompt still gets analyst findings."""
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        agent_provider="deterministic",
        matches_per_generation=2,
        rlm_enabled=True,
    )

    client = PromptCapturingClient()
    artifacts = _make_artifact_store(tmp_path, settings)
    sqlite = _make_sqlite_store(tmp_path, settings)
    orch = AgentOrchestrator(client=client, settings=settings, artifacts=artifacts, sqlite=sqlite)
    prompts = _make_prompt_bundle()

    orch.run_generation(prompts, generation_index=1, run_id="rlm_test", scenario_name="grid_ctf")

    coach_calls = [(role, prompt) for role, prompt in client.calls if role == "coach"]
    assert len(coach_calls) == 1, f"Expected 1 coach call, got {len(coach_calls)}"
    coach_prompt = coach_calls[0][1]

    # RLM analyst produces content via the REPL session; coach should still be enriched
    assert "Analyst findings (this generation)" in coach_prompt or "analyst findings" in coach_prompt.lower(), (
        "Coach prompt should contain analyst feedback marker in RLM mode"
    )


def _make_artifact_store(tmp_path: Path, settings: AppSettings) -> object:
    from autocontext.storage.artifacts import ArtifactStore

    return ArtifactStore(
        runs_root=settings.runs_root,
        knowledge_root=settings.knowledge_root,
        skills_root=settings.skills_root,
        claude_skills_path=settings.claude_skills_path,
    )


def _make_sqlite_store(tmp_path: Path, settings: AppSettings) -> object:
    from autocontext.storage.sqlite_store import SQLiteStore

    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    store = SQLiteStore(settings.db_path)
    store.migrate(migrations_dir)
    return store
