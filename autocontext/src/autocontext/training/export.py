"""Strategy-level training data export iterator (AC-170, AC-171)."""
from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore
from autocontext.training.types import MatchRecord, TrainingRecord


def export_training_data(
    sqlite: SQLiteStore,
    artifacts: ArtifactStore,
    run_id: str | None = None,
    scenario: str | None = None,
    include_matches: bool = False,
    kept_only: bool = False,
) -> Iterator[TrainingRecord | MatchRecord]:
    """Stream strategy-level training records from SQLite.

    Parameters
    ----------
    sqlite:
        The SQLite store to query.
    artifacts:
        The artifact store (for playbook/hints context).
    run_id:
        Export records for a specific run only.
    scenario:
        Export records for all runs matching this scenario.
        Ignored when *run_id* is provided.
    include_matches:
        When ``True``, also yield ``MatchRecord`` instances for each
        generation's tournament matches.
    kept_only:
        When ``True``, only yield generations where ``gate_decision == 'advance'``.

    Yields
    ------
    TrainingRecord or MatchRecord instances.
    """
    run_ids = _resolve_run_ids(sqlite, run_id=run_id, scenario=scenario)

    for rid in run_ids:
        run_scenario = _get_run_scenario(sqlite, rid)
        if run_scenario is None:
            continue

        # Load context once per run
        playbook = artifacts.read_playbook(run_scenario)
        hints = artifacts.read_hints(run_scenario)

        generations = sqlite.get_generation_metrics(rid)
        # Build a lookup of competitor outputs for this run
        competitor_outputs = _get_competitor_outputs(sqlite, rid)

        for gen in generations:
            gen_idx: int = gen["generation_index"]
            gate: str = gen["gate_decision"]

            if kept_only and gate != "advance":
                continue

            strategy = competitor_outputs.get(gen_idx, "")

            # Build trajectory up to this generation
            trajectory = _build_trajectory_snippet(generations, gen_idx)

            context: dict[str, Any] = {
                "playbook": playbook,
                "hints": hints,
                "trajectory": trajectory,
            }

            yield TrainingRecord(
                run_id=rid,
                scenario=run_scenario,
                generation_index=gen_idx,
                strategy=strategy,
                score=gen["best_score"],
                gate_decision=gate,
                context=context,
            )

            if include_matches:
                yield from _iter_matches(sqlite, rid, gen_idx)


def _resolve_run_ids(
    sqlite: SQLiteStore,
    *,
    run_id: str | None,
    scenario: str | None,
) -> list[str]:
    """Resolve which run_ids to export."""
    if run_id is not None:
        return [run_id]
    if scenario is not None:
        with sqlite.connect() as conn:
            rows = conn.execute(
                "SELECT run_id FROM runs WHERE scenario = ? ORDER BY created_at",
                (scenario,),
            ).fetchall()
            return [row["run_id"] for row in rows]
    return []


def _get_run_scenario(sqlite: SQLiteStore, run_id: str) -> str | None:
    """Look up the scenario name for a run."""
    with sqlite.connect() as conn:
        row = conn.execute(
            "SELECT scenario FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return row["scenario"] if row else None


def _get_competitor_outputs(sqlite: SQLiteStore, run_id: str) -> dict[int, str]:
    """Return a mapping of generation_index -> competitor output content."""
    rows = sqlite.get_agent_outputs_by_role(run_id, "competitor")
    result: dict[int, str] = {}
    for r in rows:
        gen_idx = r["generation_index"]
        assert isinstance(gen_idx, int)
        result[gen_idx] = str(r["content"])
    return result


def _build_trajectory_snippet(
    generations: list[dict[str, Any]] | list,  # accepts GenerationMetricsRow too
    up_to_index: int,
) -> list[dict[str, Any]]:
    """Build a score trajectory list up to (and including) the given generation."""
    return [
        {
            "generation_index": g["generation_index"],
            "best_score": g["best_score"],
            "gate_decision": g["gate_decision"],
        }
        for g in generations
        if g["generation_index"] <= up_to_index
    ]


def _iter_matches(
    sqlite: SQLiteStore,
    run_id: str,
    generation_index: int,
) -> Iterator[MatchRecord]:
    """Yield enriched MatchRecord instances for a specific generation (AC-171).

    Extracts per-turn state history from replay_json when entries contain
    a "state" key.
    """
    with sqlite.connect() as conn:
        rows = conn.execute(
            "SELECT seed, score, passed_validation, validation_errors, "
            "winner, strategy_json, replay_json "
            "FROM matches WHERE run_id = ? AND generation_index = ? ORDER BY seed",
            (run_id, generation_index),
        ).fetchall()
    for row in rows:
        replay_raw = row["replay_json"] or ""
        states = _extract_states(replay_raw)
        yield MatchRecord(
            run_id=run_id,
            generation_index=generation_index,
            seed=row["seed"],
            score=row["score"],
            passed_validation=bool(row["passed_validation"]),
            validation_errors=row["validation_errors"],
            winner=row["winner"] or None,
            strategy=row["strategy_json"] or "",
            replay_json=replay_raw,
            states=states,
        )


def _extract_states(replay_json: str) -> list[dict[str, Any]]:
    """Extract per-turn state snapshots from replay JSON.

    Looks for entries with a "state" key in the replay array.
    Returns empty list if no states found or replay is unparseable.
    """
    if not replay_json:
        return []
    try:
        replay = json.loads(replay_json)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(replay, list):
        return []
    return [
        entry["state"] for entry in replay
        if isinstance(entry, dict) and "state" in entry
    ]
