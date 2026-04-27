import type Database from "better-sqlite3";

import type { ConsultationRow, InsertConsultationOpts } from "./storage-contracts.js";

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isConsultationRow(value: unknown): value is ConsultationRow {
  return isRecord(value)
    && typeof value.id === "number"
    && typeof value.run_id === "string"
    && typeof value.generation_index === "number"
    && typeof value.trigger === "string"
    && typeof value.context_summary === "string"
    && typeof value.critique === "string"
    && typeof value.alternative_hypothesis === "string"
    && typeof value.tiebreak_recommendation === "string"
    && typeof value.suggested_next_action === "string"
    && typeof value.raw_response === "string"
    && typeof value.model_used === "string"
    && (typeof value.cost_usd === "number" || value.cost_usd === null)
    && typeof value.created_at === "string";
}

function requireConsultationRow(value: unknown): ConsultationRow {
  if (!isConsultationRow(value)) {
    throw new Error("invalid consultation row");
  }
  return value;
}

export function insertConsultationRecord(
  db: Database.Database,
  opts: InsertConsultationOpts,
): number {
  const result = db.prepare(`
    INSERT INTO consultation_log(
      run_id,
      generation_index,
      trigger,
      context_summary,
      critique,
      alternative_hypothesis,
      tiebreak_recommendation,
      suggested_next_action,
      raw_response,
      model_used,
      cost_usd
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    opts.runId,
    opts.generationIndex,
    opts.trigger,
    opts.contextSummary ?? "",
    opts.critique ?? "",
    opts.alternativeHypothesis ?? "",
    opts.tiebreakRecommendation ?? "",
    opts.suggestedNextAction ?? "",
    opts.rawResponse ?? "",
    opts.modelUsed ?? "",
    opts.costUsd ?? null,
  );
  return Number(result.lastInsertRowid);
}

export function listConsultationRecords(
  db: Database.Database,
  runId: string,
): ConsultationRow[] {
  const rows = db.prepare(`
    SELECT *
    FROM consultation_log
    WHERE run_id = ?
    ORDER BY generation_index ASC, created_at ASC, id ASC
  `).all(runId);
  return rows.map((row) => requireConsultationRow(row));
}

export function totalConsultationCostRecord(
  db: Database.Database,
  runId: string,
): number {
  const row = db.prepare(
    "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM consultation_log WHERE run_id = ?",
  ).get(runId);
  if (!isRecord(row) || typeof row.total !== "number") {
    return 0;
  }
  return row.total;
}
