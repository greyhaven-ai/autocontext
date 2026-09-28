-- autocontext/migrations/022_judge_ledger.sql
-- Judge pre-screen Phase 0 (docs/internal/judge-prescreen-design.md): one row per real improvement-loop judge call.
CREATE TABLE IF NOT EXISTS judge_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_id TEXT NOT NULL,
    run_id TEXT,
    scenario_name TEXT NOT NULL DEFAULT '',
    scenario_family TEXT NOT NULL DEFAULT '',
    round_number INTEGER NOT NULL,
    max_rounds INTEGER NOT NULL,
    quality_threshold REAL NOT NULL,
    evaluator_epoch TEXT,
    judge_identity TEXT,
    evaluator_spec_json TEXT,
    rubric_hash TEXT NOT NULL,
    task_prompt_hash TEXT NOT NULL,
    task_prompt TEXT NOT NULL,
    output_hash TEXT NOT NULL,
    output TEXT NOT NULL,
    required_concepts_json TEXT NOT NULL DEFAULT '[]',
    score REAL NOT NULL,
    passed INTEGER NOT NULL,
    dimension_scores_json TEXT NOT NULL DEFAULT '{}',
    internal_retries INTEGER NOT NULL DEFAULT 0,
    judge_failed INTEGER NOT NULL DEFAULT 0,
    previous_round_number INTEGER,
    previous_score REAL,
    previous_dimension_scores_json TEXT,
    previous_output_hash TEXT,
    fixture_provenance_json TEXT NOT NULL DEFAULT '{}',
    execution_provenance_json TEXT NOT NULL DEFAULT '{}',
    evaluator_guardrail_json TEXT,
    prescreen_json TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_judge_ledger_epoch_family ON judge_ledger(evaluator_epoch, scenario_family);
CREATE INDEX IF NOT EXISTS idx_judge_ledger_identity_family ON judge_ledger(judge_identity, scenario_family);
CREATE INDEX IF NOT EXISTS idx_judge_ledger_loop ON judge_ledger(loop_id, round_number);
