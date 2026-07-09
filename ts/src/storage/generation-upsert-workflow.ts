import type Database from "better-sqlite3";

import type { UpsertGenerationRecordOpts } from "./generation-record-contracts.js";

export function upsertGenerationRecord(
  db: Database.Database,
  runId: string,
  generationIndex: number,
  opts: UpsertGenerationRecordOpts,
): void {
  db.prepare(
    `INSERT INTO generations(
       run_id, generation_index, mean_score, best_score, elo, wins, losses,
       gate_decision, status, duration_seconds, dimension_summary_json,
       scoring_backend, rating_uncertainty, evaluator_epoch
     )
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
       evaluator_epoch = excluded.evaluator_epoch,
       updated_at = datetime('now')`,
  ).run(
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
    opts.evaluatorEpoch ?? null,
  );
}

export function getGenerationRecords<T>(db: Database.Database, runId: string): T[] {
  return db
    .prepare(`SELECT * FROM generations WHERE run_id = ? ORDER BY generation_index`)
    .all(runId) as T[];
}

export function getBestGenerationForScenarioRecord<T>(
  db: Database.Database,
  scenario: string,
): T | null {
  return (
    (db
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
      .get(scenario) as T | undefined) ?? null
  );
}
