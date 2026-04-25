import type Database from "better-sqlite3";

import type { NotebookRow, UpsertNotebookOpts } from "./storage-contracts.js";

const NOTEBOOK_JSON_FIELDS = [
  "current_hypotheses",
  "unresolved_questions",
  "operator_observations",
  "follow_ups",
] as const;

type NotebookJsonField = typeof NOTEBOOK_JSON_FIELDS[number];
type RawNotebookRow = Omit<NotebookRow, NotebookJsonField> & Record<NotebookJsonField, string>;

function parseJsonArray(raw: unknown): string[] {
  if (typeof raw !== "string") {
    return [];
  }
  try {
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed)
      ? parsed.filter((value): value is string => typeof value === "string")
      : [];
  } catch {
    return [];
  }
}

function parseNotebookRow(row: RawNotebookRow): NotebookRow {
  return {
    ...row,
    current_hypotheses: parseJsonArray(row.current_hypotheses),
    unresolved_questions: parseJsonArray(row.unresolved_questions),
    operator_observations: parseJsonArray(row.operator_observations),
    follow_ups: parseJsonArray(row.follow_ups),
  };
}

export function upsertNotebookRecord(
  db: Database.Database,
  opts: UpsertNotebookOpts,
): void {
  const existing = getNotebookRecord(db, opts.sessionId);
  const currentObjective = opts.currentObjective ?? existing?.current_objective ?? "";
  const currentHypotheses = opts.currentHypotheses ?? existing?.current_hypotheses ?? [];
  const bestRunId = opts.bestRunId ?? existing?.best_run_id ?? null;
  const bestGeneration = opts.bestGeneration ?? existing?.best_generation ?? null;
  const bestScore = opts.bestScore ?? existing?.best_score ?? null;
  const unresolvedQuestions = opts.unresolvedQuestions ?? existing?.unresolved_questions ?? [];
  const operatorObservations = opts.operatorObservations ?? existing?.operator_observations ?? [];
  const followUps = opts.followUps ?? existing?.follow_ups ?? [];

  db.prepare(`
    INSERT INTO session_notebooks(
      session_id, scenario_name, current_objective, current_hypotheses,
      best_run_id, best_generation, best_score,
      unresolved_questions, operator_observations, follow_ups
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(session_id) DO UPDATE SET
      scenario_name = excluded.scenario_name,
      current_objective = excluded.current_objective,
      current_hypotheses = excluded.current_hypotheses,
      best_run_id = excluded.best_run_id,
      best_generation = excluded.best_generation,
      best_score = excluded.best_score,
      unresolved_questions = excluded.unresolved_questions,
      operator_observations = excluded.operator_observations,
      follow_ups = excluded.follow_ups,
      updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
  `).run(
    opts.sessionId,
    opts.scenarioName,
    currentObjective,
    JSON.stringify(currentHypotheses),
    bestRunId,
    bestGeneration,
    bestScore,
    JSON.stringify(unresolvedQuestions),
    JSON.stringify(operatorObservations),
    JSON.stringify(followUps),
  );
}

export function getNotebookRecord(
  db: Database.Database,
  sessionId: string,
): NotebookRow | null {
  const row = db.prepare(
    "SELECT * FROM session_notebooks WHERE session_id = ?",
  ).get(sessionId) as RawNotebookRow | undefined;
  return row ? parseNotebookRow(row) : null;
}

export function listNotebookRecords(db: Database.Database): NotebookRow[] {
  const rows = db.prepare(
    "SELECT * FROM session_notebooks ORDER BY updated_at DESC",
  ).all() as RawNotebookRow[];
  return rows.map((row) => parseNotebookRow(row));
}

export function deleteNotebookRecord(
  db: Database.Database,
  sessionId: string,
): boolean {
  const result = db.prepare("DELETE FROM session_notebooks WHERE session_id = ?").run(sessionId);
  return result.changes > 0;
}
