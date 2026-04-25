import type Database from "better-sqlite3";

import type {
  InsertMonitorAlertOpts,
  InsertMonitorConditionOpts,
  MonitorAlertRow,
  MonitorConditionRow,
} from "./storage-contracts.js";

type RawMonitorConditionRow = Omit<MonitorConditionRow, "params"> & {
  params_json: string;
};

type RawMonitorAlertRow = Omit<MonitorAlertRow, "payload"> & {
  payload_json: string;
};

function parseRecordJson(raw: unknown): Record<string, unknown> {
  if (typeof raw !== "string") {
    return {};
  }
  try {
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function parseConditionRow(row: RawMonitorConditionRow): MonitorConditionRow {
  const { params_json: paramsJson, ...rest } = row;
  return { ...rest, params: parseRecordJson(paramsJson) };
}

function parseAlertRow(row: RawMonitorAlertRow): MonitorAlertRow {
  const { payload_json: payloadJson, ...rest } = row;
  return { ...rest, payload: parseRecordJson(payloadJson) };
}

export function insertMonitorConditionRecord(
  db: Database.Database,
  opts: InsertMonitorConditionOpts,
): string {
  db.prepare(`
    INSERT INTO monitor_conditions(id, name, condition_type, params_json, scope, active)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(
    opts.id,
    opts.name,
    opts.conditionType,
    JSON.stringify(opts.params ?? {}),
    opts.scope ?? "global",
    opts.active === false ? 0 : 1,
  );
  return opts.id;
}

export function listMonitorConditionRecords(
  db: Database.Database,
  opts: { activeOnly?: boolean; scope?: string } = {},
): MonitorConditionRow[] {
  let query = "SELECT * FROM monitor_conditions WHERE 1=1";
  const params: unknown[] = [];
  if (opts.activeOnly ?? true) {
    query += " AND active = 1";
  }
  if (opts.scope !== undefined) {
    query += " AND scope = ?";
    params.push(opts.scope);
  }
  query += " ORDER BY created_at DESC";
  const rows = db.prepare(query).all(...params) as RawMonitorConditionRow[];
  return rows.map((row) => parseConditionRow(row));
}

export function countMonitorConditionRecords(
  db: Database.Database,
  opts: { activeOnly?: boolean; scope?: string } = {},
): number {
  let query = "SELECT COUNT(*) AS cnt FROM monitor_conditions WHERE 1=1";
  const params: unknown[] = [];
  if (opts.activeOnly ?? true) {
    query += " AND active = 1";
  }
  if (opts.scope !== undefined) {
    query += " AND scope = ?";
    params.push(opts.scope);
  }
  const row = db.prepare(query).get(...params) as { cnt: number } | undefined;
  return row?.cnt ?? 0;
}

export function getMonitorConditionRecord(
  db: Database.Database,
  conditionId: string,
): MonitorConditionRow | null {
  const row = db.prepare(
    "SELECT * FROM monitor_conditions WHERE id = ?",
  ).get(conditionId) as RawMonitorConditionRow | undefined;
  return row ? parseConditionRow(row) : null;
}

export function deactivateMonitorConditionRecord(
  db: Database.Database,
  conditionId: string,
): boolean {
  const result = db.prepare(
    "UPDATE monitor_conditions SET active = 0 WHERE id = ?",
  ).run(conditionId);
  return result.changes > 0;
}

export function insertMonitorAlertRecord(
  db: Database.Database,
  opts: InsertMonitorAlertOpts,
): string {
  db.prepare(`
    INSERT INTO monitor_alerts(
      id, condition_id, condition_name, condition_type, scope, detail, payload_json, fired_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
  `).run(
    opts.id,
    opts.conditionId,
    opts.conditionName,
    opts.conditionType,
    opts.scope ?? "global",
    opts.detail ?? "",
    JSON.stringify(opts.payload ?? {}),
    opts.firedAt ?? new Date().toISOString(),
  );
  return opts.id;
}

export function listMonitorAlertRecords(
  db: Database.Database,
  opts: {
    conditionId?: string;
    scope?: string;
    limit?: number;
    since?: string;
  } = {},
): MonitorAlertRow[] {
  let query = "SELECT * FROM monitor_alerts WHERE 1=1";
  const params: unknown[] = [];
  if (opts.conditionId !== undefined) {
    query += " AND condition_id = ?";
    params.push(opts.conditionId);
  }
  if (opts.scope !== undefined) {
    query += " AND scope = ?";
    params.push(opts.scope);
  }
  if (opts.since !== undefined) {
    query += " AND fired_at >= ?";
    params.push(opts.since);
  }
  query += " ORDER BY fired_at DESC LIMIT ?";
  params.push(opts.limit ?? 100);
  const rows = db.prepare(query).all(...params) as RawMonitorAlertRow[];
  return rows.map((row) => parseAlertRow(row));
}

export function getLatestMonitorAlertRecord(
  db: Database.Database,
  conditionId: string,
): MonitorAlertRow | null {
  return listMonitorAlertRecords(db, { conditionId, limit: 1 })[0] ?? null;
}
