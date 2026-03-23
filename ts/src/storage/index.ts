/**
 * SQLite storage for autocontext.
 * Uses better-sqlite3 for synchronous access (same as Python's sqlite3).
 * Covers: task queue, human feedback, and generation loop CRUD (AC-342).
 */

import Database from "better-sqlite3";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

export interface TaskQueueRow {
  id: string;
  spec_name: string;
  status: string;
  priority: number;
  config_json: string | null;
  scheduled_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  best_score: number | null;
  best_output: string | null;
  total_rounds: number | null;
  met_threshold: number;
  result_json: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface HumanFeedbackRow {
  id: number;
  scenario_name: string;
  generation_id: string | null;
  agent_output: string;
  human_score: number | null;
  human_notes: string;
  created_at: string;
}

export class SQLiteStore {
  private db: Database.Database;

  constructor(dbPath: string) {
    this.db = new Database(dbPath);
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("foreign_keys = ON");
  }

  migrate(migrationsDir: string): void {
    this.db.exec(
      `CREATE TABLE IF NOT EXISTS schema_version (
         filename TEXT PRIMARY KEY,
         applied_at TEXT NOT NULL DEFAULT (datetime('now'))
       )`,
    );

    const applied = new Set(
      (this.db.prepare("SELECT filename FROM schema_version").all() as Array<{ filename: string }>)
        .map(r => r.filename),
    );

    const files = readdirSync(migrationsDir)
      .filter(f => f.endsWith(".sql"))
      .sort();

    for (const file of files) {
      if (applied.has(file)) continue;
      const sql = readFileSync(join(migrationsDir, file), "utf8");
      this.db.exec(sql);
      this.db.prepare("INSERT INTO schema_version(filename) VALUES (?)").run(file);
    }
  }

  enqueueTask(
    id: string,
    specName: string,
    priority = 0,
    config?: Record<string, unknown>,
    scheduledAt?: string,
  ): void {
    const configJson = config ? JSON.stringify(config) : null;
    this.db
      .prepare(
        `INSERT INTO task_queue(id, spec_name, priority, config_json, scheduled_at)
         VALUES (?, ?, ?, ?, ?)`,
      )
      .run(id, specName, priority, configJson, scheduledAt ?? null);
  }

  dequeueTask(): TaskQueueRow | null {
    const tx = this.db.transaction(() => {
      const row = this.db
        .prepare(
          `SELECT id FROM task_queue
           WHERE status = 'pending'
             AND (scheduled_at IS NULL OR scheduled_at <= datetime('now'))
           ORDER BY priority DESC, created_at ASC
           LIMIT 1`,
        )
        .get() as { id: string } | undefined;

      if (!row) return null;

      const changes = this.db
        .prepare(
          `UPDATE task_queue
           SET status = 'running',
               started_at = datetime('now'),
               updated_at = datetime('now')
           WHERE id = ? AND status = 'pending'`,
        )
        .run(row.id);

      if (changes.changes === 0) return null;

      return this.db
        .prepare("SELECT * FROM task_queue WHERE id = ?")
        .get(row.id) as TaskQueueRow | undefined ?? null;
    });

    return tx();
  }

  completeTask(
    taskId: string,
    bestScore: number,
    bestOutput: string,
    totalRounds: number,
    metThreshold: boolean,
    resultJson?: string,
  ): void {
    this.db
      .prepare(
        `UPDATE task_queue
         SET status = 'completed',
             completed_at = datetime('now'),
             updated_at = datetime('now'),
             best_score = ?,
             best_output = ?,
             total_rounds = ?,
             met_threshold = ?,
             result_json = ?
         WHERE id = ?`,
      )
      .run(bestScore, bestOutput, totalRounds, metThreshold ? 1 : 0, resultJson ?? null, taskId);
  }

  failTask(taskId: string, error: string): void {
    this.db
      .prepare(
        `UPDATE task_queue
         SET status = 'failed',
             completed_at = datetime('now'),
             updated_at = datetime('now'),
             error = ?
         WHERE id = ?`,
      )
      .run(error, taskId);
  }

  pendingTaskCount(): number {
    const row = this.db
      .prepare("SELECT COUNT(*) as cnt FROM task_queue WHERE status = 'pending'")
      .get() as { cnt: number };
    return row.cnt;
  }

  getTask(taskId: string): TaskQueueRow | null {
    return (
      (this.db
        .prepare("SELECT * FROM task_queue WHERE id = ?")
        .get(taskId) as TaskQueueRow | undefined) ?? null
    );
  }

  // ---- Human Feedback ----

  insertHumanFeedback(
    scenarioName: string,
    agentOutput: string,
    humanScore?: number | null,
    humanNotes = "",
    generationId?: string | null,
  ): number {
    if (humanScore != null && (humanScore < 0 || humanScore > 1)) {
      throw new Error(`human_score must be in [0.0, 1.0], got ${humanScore}`);
    }
    const result = this.db
      .prepare(
        `INSERT INTO human_feedback(scenario_name, generation_id, agent_output, human_score, human_notes)
         VALUES (?, ?, ?, ?, ?)`,
      )
      .run(scenarioName, generationId ?? null, agentOutput, humanScore ?? null, humanNotes);
    return Number(result.lastInsertRowid);
  }

  getHumanFeedback(scenarioName: string, limit = 10): HumanFeedbackRow[] {
    return this.db
      .prepare(
        `SELECT id, scenario_name, generation_id, agent_output, human_score, human_notes, created_at
         FROM human_feedback
         WHERE scenario_name = ?
         ORDER BY created_at DESC
         LIMIT ?`,
      )
      .all(scenarioName, limit) as HumanFeedbackRow[];
  }

  getCalibrationExamples(scenarioName: string, limit = 5): HumanFeedbackRow[] {
    return this.db
      .prepare(
        `SELECT id, scenario_name, agent_output, human_score, human_notes, created_at
         FROM human_feedback
         WHERE scenario_name = ? AND human_score IS NOT NULL AND human_notes != ''
         ORDER BY created_at DESC
         LIMIT ?`,
      )
      .all(scenarioName, limit) as HumanFeedbackRow[];
  }

  // ---- Generation Loop CRUD (AC-342) ----

  createRun(
    runId: string,
    scenario: string,
    generations: number,
    executorMode: string,
    agentProvider = "",
  ): void {
    this.db
      .prepare(
        `INSERT OR IGNORE INTO runs(run_id, scenario, target_generations, executor_mode, status, agent_provider)
         VALUES (?, ?, ?, ?, 'running', ?)`,
      )
      .run(runId, scenario, generations, executorMode, agentProvider);
  }

  getRun(runId: string): RunRow | null {
    return (
      (this.db
        .prepare("SELECT * FROM runs WHERE run_id = ?")
        .get(runId) as RunRow | undefined) ?? null
    );
  }

  updateRunStatus(runId: string, status: string): void {
    this.db
      .prepare(
        `UPDATE runs
         SET status = ?,
             updated_at = datetime('now')
         WHERE run_id = ?`,
      )
      .run(status, runId);
  }

  upsertGeneration(
    runId: string,
    generationIndex: number,
    opts: UpsertGenerationOpts,
  ): void {
    this.db
      .prepare(
        `INSERT INTO generations(
           run_id, generation_index, mean_score, best_score, elo, wins, losses,
           gate_decision, status, duration_seconds, dimension_summary_json,
           scoring_backend, rating_uncertainty
         )
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(run_id, generation_index) DO UPDATE SET
           mean_score = excluded.mean_score,
           best_score = excluded.best_score,
           elo = excluded.elo,
           wins = excluded.wins,
           losses = excluded.losses,
           gate_decision = excluded.gate_decision,
           status = excluded.status,
           duration_seconds = excluded.duration_seconds,
           dimension_summary_json = excluded.dimension_summary_json,
           scoring_backend = excluded.scoring_backend,
           rating_uncertainty = excluded.rating_uncertainty,
           updated_at = datetime('now')`,
      )
      .run(
        runId,
        generationIndex,
        opts.meanScore,
        opts.bestScore,
        opts.elo,
        opts.wins,
        opts.losses,
        opts.gateDecision,
        opts.status,
        opts.durationSeconds ?? null,
        opts.dimensionSummaryJson ?? null,
        opts.scoringBackend ?? "elo",
        opts.ratingUncertainty ?? null,
      );
  }

  getGenerations(runId: string): GenerationRow[] {
    return this.db
      .prepare(
        `SELECT * FROM generations WHERE run_id = ? ORDER BY generation_index`,
      )
      .all(runId) as GenerationRow[];
  }

  countCompletedRuns(scenario: string): number {
    const row = this.db
      .prepare(
        `SELECT COUNT(*) as cnt
         FROM runs
         WHERE scenario = ? AND status = 'completed'`,
      )
      .get(scenario) as { cnt: number };
    return row.cnt;
  }

  getBestGenerationForScenario(
    scenario: string,
  ): (GenerationRow & { run_id: string }) | null {
    return (
      (this.db
        .prepare(
          `SELECT g.*
           FROM generations g
           JOIN runs r ON r.run_id = g.run_id
           WHERE r.scenario = ?
             AND r.status = 'completed'
             AND g.status = 'completed'
           ORDER BY g.best_score DESC, g.elo DESC, g.updated_at DESC
           LIMIT 1`,
        )
        .get(scenario) as (GenerationRow & { run_id: string }) | undefined) ?? null
    );
  }

  getBestMatchForScenario(scenario: string): MatchRow | null {
    return (
      (this.db
        .prepare(
          `SELECT m.*
           FROM matches m
           JOIN runs r ON r.run_id = m.run_id
           WHERE r.scenario = ?
             AND r.status = 'completed'
             AND m.strategy_json != ''
           ORDER BY m.score DESC, m.created_at DESC
           LIMIT 1`,
        )
        .get(scenario) as MatchRow | undefined) ?? null
    );
  }

  recordMatch(
    runId: string,
    generationIndex: number,
    opts: RecordMatchOpts,
  ): void {
    this.db
      .prepare(
        `INSERT INTO matches(
           run_id, generation_index, seed, score,
           passed_validation, validation_errors,
           winner, strategy_json, replay_json
         )
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        runId,
        generationIndex,
        opts.seed,
        opts.score,
        opts.passedValidation ? 1 : 0,
        opts.validationErrors,
        opts.winner ?? "",
        opts.strategyJson ?? "",
        opts.replayJson ?? "",
      );
  }

  getMatchesForRun(runId: string): MatchRow[] {
    return this.db
      .prepare("SELECT * FROM matches WHERE run_id = ? ORDER BY id")
      .all(runId) as MatchRow[];
  }

  appendAgentOutput(
    runId: string,
    generationIndex: number,
    role: string,
    content: string,
  ): void {
    this.db
      .prepare(
        `INSERT INTO agent_outputs(run_id, generation_index, role, content)
         VALUES (?, ?, ?, ?)`,
      )
      .run(runId, generationIndex, role, content);
  }

  getAgentOutputs(
    runId: string,
    generationIndex: number,
  ): AgentOutputRow[] {
    return this.db
      .prepare(
        `SELECT * FROM agent_outputs
         WHERE run_id = ? AND generation_index = ?
         ORDER BY id`,
      )
      .all(runId, generationIndex) as AgentOutputRow[];
  }

  getScoreTrajectory(runId: string): TrajectoryRow[] {
    const rows = this.db
      .prepare(
        `SELECT
           generation_index, mean_score, best_score, elo,
           gate_decision, dimension_summary_json,
           scoring_backend, rating_uncertainty
         FROM generations
         WHERE run_id = ? AND status = 'completed'
         ORDER BY generation_index`,
      )
      .all(runId) as Array<Record<string, unknown>>;

    const result: TrajectoryRow[] = [];
    let prevBest = 0.0;
    for (const row of rows) {
      const bestScore = row.best_score as number;
      const delta = bestScore - prevBest;
      prevBest = bestScore;

      let dimensionSummary: Record<string, unknown> = {};
      const rawDimensionSummary = row.dimension_summary_json;
      if (typeof rawDimensionSummary === "string" && rawDimensionSummary) {
        try {
          dimensionSummary = JSON.parse(rawDimensionSummary);
        } catch {
          dimensionSummary = {};
        }
      }

      result.push({
        generation_index: row.generation_index as number,
        mean_score: row.mean_score as number,
        best_score: bestScore,
        elo: row.elo as number,
        gate_decision: row.gate_decision as string,
        delta,
        dimension_summary: dimensionSummary,
        scoring_backend: (row.scoring_backend as string) ?? "elo",
        rating_uncertainty: (row.rating_uncertainty as number | null) ?? null,
      });
    }
    return result;
  }

  listRuns(limit = 50, scenario?: string): RunRow[] {
    if (scenario) {
      return this.db
        .prepare(
          `SELECT * FROM runs WHERE scenario = ? ORDER BY created_at DESC LIMIT ?`,
        )
        .all(scenario, limit) as RunRow[];
    }
    return this.db
      .prepare(`SELECT * FROM runs ORDER BY created_at DESC LIMIT ?`)
      .all(limit) as RunRow[];
  }

  listRunsForScenario(scenario: string): RunRow[] {
    return this.db
      .prepare(
        `SELECT * FROM runs WHERE scenario = ? ORDER BY created_at ASC`,
      )
      .all(scenario) as RunRow[];
  }

  getMatchesForGeneration(runId: string, generationIndex: number): MatchRow[] {
    return this.db
      .prepare(
        `SELECT * FROM matches WHERE run_id = ? AND generation_index = ? ORDER BY id`,
      )
      .all(runId, generationIndex) as MatchRow[];
  }

  close(): void {
    this.db.close();
  }
}

// ---- Row interfaces for generation loop ----

export interface RunRow {
  run_id: string;
  scenario: string;
  target_generations: number;
  executor_mode: string;
  status: string;
  agent_provider: string;
  created_at: string;
  updated_at: string;
}

export interface GenerationRow {
  run_id: string;
  generation_index: number;
  mean_score: number;
  best_score: number;
  elo: number;
  wins: number;
  losses: number;
  gate_decision: string;
  status: string;
  duration_seconds: number | null;
  dimension_summary_json: string | null;
  scoring_backend: string;
  rating_uncertainty: number | null;
  created_at: string;
  updated_at: string;
}

export interface MatchRow {
  id: number;
  run_id: string;
  generation_index: number;
  seed: number;
  score: number;
  passed_validation: number;
  validation_errors: string;
  winner: string;
  strategy_json: string;
  replay_json: string;
  created_at: string;
}

export interface AgentOutputRow {
  id: number;
  run_id: string;
  generation_index: number;
  role: string;
  content: string;
  created_at: string;
}

export interface TrajectoryRow {
  generation_index: number;
  mean_score: number;
  best_score: number;
  elo: number;
  gate_decision: string;
  delta: number;
  dimension_summary: Record<string, unknown>;
  scoring_backend: string;
  rating_uncertainty: number | null;
}

export interface UpsertGenerationOpts {
  meanScore: number;
  bestScore: number;
  elo: number;
  wins: number;
  losses: number;
  gateDecision: string;
  status: string;
  durationSeconds?: number | null;
  dimensionSummaryJson?: string | null;
  scoringBackend?: string;
  ratingUncertainty?: number | null;
}

export interface RecordMatchOpts {
  seed: number;
  score: number;
  passedValidation: boolean;
  validationErrors: string;
  winner?: string;
  strategyJson?: string;
  replayJson?: string;
}
