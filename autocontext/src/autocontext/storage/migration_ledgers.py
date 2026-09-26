from __future__ import annotations

# Mirrors TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES in
# ts/src/storage/storage-migration-workflow.ts. tests/test_cross_runtime_migration_ledgers.py
# fails when the two maps differ.
TYPESCRIPT_TO_PYTHON_BASELINES: dict[str, tuple[str, ...]] = {
    "007_task_queue.sql": ("007_task_queue.sql",),
    "008_human_feedback.sql": ("006_human_feedback.sql",),
    "009_generation_loop.sql": (
        "001_initial.sql",
        "002_phase3_phase7.sql",
        "003_agent_subagent_metadata.sql",
        "004_knowledge_inheritance.sql",
        "005_ecosystem_provider_tracking.sql",
        "009_generation_timing.sql",
        "013_generation_dimension_summary.sql",
        "014_scoring_backend_metadata.sql",
        "015_match_replay.sql",
    ),
    "010_session_notebook.sql": ("010_session_notebook.sql",),
    "011_monitors.sql": ("011_monitors.sql",),
    "012_consultation_log.sql": ("010_consultation_log.sql",),
    "012_research_hub.sql": ("012_research_hub.sql",),
    "014_generation_evaluator_epoch.sql": ("016_generation_evaluator_epoch.sql",),
    "015_generation_quarantined.sql": ("017_generation_quarantined.sql",),
    "016_generation_score_revisions.sql": ("018_generation_score_revisions.sql",),
    "017_task_queue_attempts.sql": ("019_task_queue_attempts.sql",),
    "019_run_minimum_generations.sql": ("020_run_minimum_generations.sql",),
    "020_human_feedback_acquisition.sql": ("021_human_feedback_acquisition.sql",),
}

PYTHON_TO_TYPESCRIPT_BASELINES: dict[str, tuple[str, ...]] = {
    python_migration: (typescript_migration,)
    for typescript_migration, python_migrations in TYPESCRIPT_TO_PYTHON_BASELINES.items()
    for python_migration in python_migrations
}

TYPESCRIPT_BASELINE_MIGRATIONS: tuple[str, ...] = tuple(TYPESCRIPT_TO_PYTHON_BASELINES)


def typescript_baselines_for_python_migrations(applied_python_migrations: set[str]) -> tuple[str, ...]:
    return tuple(
        typescript_migration
        for typescript_migration, python_migrations in TYPESCRIPT_TO_PYTHON_BASELINES.items()
        if all(migration in applied_python_migrations for migration in python_migrations)
    )
