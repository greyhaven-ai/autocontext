"""TypedDict definitions for SQLite row shapes (AC-485).

Replaces untyped dict[str, Any] returns with named, documented row types.
Each TypedDict mirrors the corresponding SQL table schema.
"""

from __future__ import annotations

from typing import TypedDict


class RunRow(TypedDict):
    """Row from the ``runs`` table (list_runs query subset)."""

    run_id: str
    scenario: str
    target_generations: int
    executor_mode: str
    status: str
    created_at: str


class GenerationMetricsRow(TypedDict):
    """Row from the ``generations`` table (full columns)."""

    run_id: str
    generation_index: int
    mean_score: float
    best_score: float
    elo: float
    wins: int
    losses: int
    gate_decision: str
    status: str
    duration_seconds: float | None
    scoring_backend: str | None
    rating_uncertainty: float | None
    dimension_summary_json: str | None
    created_at: str
    updated_at: str


class MatchRow(TypedDict):
    """Row from the ``matches`` table."""

    id: int
    run_id: str
    generation_index: int
    seed: int
    score: float
    winner: str
    strategy_json: str
    replay_json: str
    passed_validation: int
    validation_errors: str
    created_at: str


class KnowledgeSnapshotRow(TypedDict):
    """Row from the ``knowledge_snapshots`` table."""

    scenario: str
    run_id: str
    best_score: float
    best_elo: float
    playbook_hash: str
    agent_provider: str
    rlm_enabled: int
    scoring_backend: str
    rating_uncertainty: float | None
    created_at: str


class AgentOutputRow(TypedDict):
    """Row from the ``agent_outputs`` table."""

    id: int
    run_id: str
    generation_index: int
    role: str
    content: str
    created_at: str


class HumanFeedbackRow(TypedDict):
    """Row from the ``human_feedback`` table."""

    id: int
    scenario_name: str
    agent_output: str
    human_score: float | None
    human_notes: str
    generation_id: str | None
    created_at: str


class TaskQueueRow(TypedDict):
    """Row from the ``task_queue`` table."""

    id: str
    spec_name: str
    priority: int
    config_json: str | None
    status: str
    scheduled_at: str | None
    started_at: str | None
    completed_at: str | None
    best_score: float | None
    best_output: str | None
    total_rounds: int
    met_threshold: int
    result_json: str | None
    error: str | None
    created_at: str
