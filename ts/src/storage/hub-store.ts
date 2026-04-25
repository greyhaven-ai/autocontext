import type Database from "better-sqlite3";

import type {
  HubPackageRecordRow,
  HubPromotionRecordRow,
  HubResultRecordRow,
  HubSessionRow,
  SaveHubPackageRecordOpts,
  SaveHubPromotionRecordOpts,
  SaveHubResultRecordOpts,
  UpsertHubSessionOpts,
} from "./storage-contracts.js";

type RawHubSessionRow = Omit<HubSessionRow, "shared" | "metadata"> & {
  shared: number;
  metadata_json: string;
};

type RawHubPackageRecordRow = Omit<HubPackageRecordRow, "tags" | "metadata"> & {
  tags_json: string;
  metadata_json: string;
};

type RawHubResultRecordRow = Omit<HubResultRecordRow, "tags" | "metadata"> & {
  tags_json: string;
  metadata_json: string;
};

type RawHubPromotionRecordRow = Omit<HubPromotionRecordRow, "metadata"> & {
  metadata_json: string;
};

function nowIso(): string {
  return new Date().toISOString();
}

function parseJsonRecord(raw: unknown): Record<string, unknown> {
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

function parseJsonStringArray(raw: unknown): string[] {
  if (typeof raw !== "string") {
    return [];
  }
  try {
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed)
      ? parsed.filter((entry): entry is string => typeof entry === "string")
      : [];
  } catch {
    return [];
  }
}

function parseHubSessionRow(row: RawHubSessionRow): HubSessionRow {
  const { metadata_json: metadataJson, ...rest } = row;
  return {
    ...rest,
    shared: Boolean(row.shared),
    metadata: parseJsonRecord(metadataJson),
  };
}

function parseHubPackageRow(row: RawHubPackageRecordRow): HubPackageRecordRow {
  const { tags_json: tagsJson, metadata_json: metadataJson, ...rest } = row;
  return {
    ...rest,
    tags: parseJsonStringArray(tagsJson),
    metadata: parseJsonRecord(metadataJson),
  };
}

function parseHubResultRow(row: RawHubResultRecordRow): HubResultRecordRow {
  const { tags_json: tagsJson, metadata_json: metadataJson, ...rest } = row;
  return {
    ...rest,
    tags: parseJsonStringArray(tagsJson),
    metadata: parseJsonRecord(metadataJson),
  };
}

function parseHubPromotionRow(row: RawHubPromotionRecordRow): HubPromotionRecordRow {
  const { metadata_json: metadataJson, ...rest } = row;
  return {
    ...rest,
    metadata: parseJsonRecord(metadataJson),
  };
}

export function upsertHubSessionRecord(
  db: Database.Database,
  sessionId: string,
  opts: UpsertHubSessionOpts,
): void {
  const existing = getHubSessionRecord(db, sessionId);
  const owner = opts.owner ?? existing?.owner ?? "";
  const status = opts.status ?? existing?.status ?? "active";
  const leaseExpiresAt = opts.leaseExpiresAt ?? existing?.lease_expires_at ?? "";
  const lastHeartbeatAt = opts.lastHeartbeatAt ?? existing?.last_heartbeat_at ?? "";
  const shared = opts.shared ?? existing?.shared ?? false;
  const externalLink = opts.externalLink ?? existing?.external_link ?? "";
  const metadata = opts.metadata ?? existing?.metadata ?? {};

  db.prepare(`
    INSERT INTO hub_sessions(
      session_id, owner, status, lease_expires_at, last_heartbeat_at,
      shared, external_link, metadata_json
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(session_id) DO UPDATE SET
      owner = excluded.owner,
      status = excluded.status,
      lease_expires_at = excluded.lease_expires_at,
      last_heartbeat_at = excluded.last_heartbeat_at,
      shared = excluded.shared,
      external_link = excluded.external_link,
      metadata_json = excluded.metadata_json,
      updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
  `).run(
    sessionId,
    owner,
    status,
    leaseExpiresAt,
    lastHeartbeatAt,
    shared ? 1 : 0,
    externalLink,
    JSON.stringify(metadata),
  );
}

export function heartbeatHubSessionRecord(
  db: Database.Database,
  sessionId: string,
  opts: { lastHeartbeatAt: string; leaseExpiresAt?: string | null },
): void {
  const existing = getHubSessionRecord(db, sessionId);
  upsertHubSessionRecord(db, sessionId, {
    owner: existing?.owner,
    status: existing?.status,
    leaseExpiresAt: opts.leaseExpiresAt ?? existing?.lease_expires_at ?? "",
    lastHeartbeatAt: opts.lastHeartbeatAt,
    shared: existing?.shared,
    externalLink: existing?.external_link,
    metadata: existing?.metadata,
  });
}

export function getHubSessionRecord(
  db: Database.Database,
  sessionId: string,
): HubSessionRow | null {
  const row = db.prepare(
    "SELECT * FROM hub_sessions WHERE session_id = ?",
  ).get(sessionId) as RawHubSessionRow | undefined;
  return row ? parseHubSessionRow(row) : null;
}

export function listHubSessionRecords(db: Database.Database): HubSessionRow[] {
  const rows = db.prepare(
    "SELECT * FROM hub_sessions ORDER BY updated_at DESC",
  ).all() as RawHubSessionRow[];
  return rows.map((row) => parseHubSessionRow(row));
}

export function saveHubPackageRecord(
  db: Database.Database,
  opts: SaveHubPackageRecordOpts,
): void {
  db.prepare(`
    INSERT INTO hub_packages(
      package_id, scenario_name, scenario_family, source_run_id, source_generation,
      title, description, promotion_level, best_score, best_elo,
      payload_path, strategy_package_path, tags_json, metadata_json, created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(package_id) DO UPDATE SET
      scenario_name = excluded.scenario_name,
      scenario_family = excluded.scenario_family,
      source_run_id = excluded.source_run_id,
      source_generation = excluded.source_generation,
      title = excluded.title,
      description = excluded.description,
      promotion_level = excluded.promotion_level,
      best_score = excluded.best_score,
      best_elo = excluded.best_elo,
      payload_path = excluded.payload_path,
      strategy_package_path = excluded.strategy_package_path,
      tags_json = excluded.tags_json,
      metadata_json = excluded.metadata_json,
      updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
  `).run(
    opts.packageId,
    opts.scenarioName,
    opts.scenarioFamily,
    opts.sourceRunId,
    opts.sourceGeneration,
    opts.title,
    opts.description,
    opts.promotionLevel,
    opts.bestScore,
    opts.bestElo,
    opts.payloadPath,
    opts.strategyPackagePath,
    JSON.stringify(opts.tags),
    JSON.stringify(opts.metadata ?? {}),
    opts.createdAt || nowIso(),
  );
}

export function getHubPackageRecord(
  db: Database.Database,
  packageId: string,
): HubPackageRecordRow | null {
  const row = db.prepare(
    "SELECT * FROM hub_packages WHERE package_id = ?",
  ).get(packageId) as RawHubPackageRecordRow | undefined;
  return row ? parseHubPackageRow(row) : null;
}

export function listHubPackageRecords(db: Database.Database): HubPackageRecordRow[] {
  const rows = db.prepare(
    "SELECT * FROM hub_packages ORDER BY created_at DESC",
  ).all() as RawHubPackageRecordRow[];
  return rows.map((row) => parseHubPackageRow(row));
}

export function saveHubResultRecord(
  db: Database.Database,
  opts: SaveHubResultRecordOpts,
): void {
  db.prepare(`
    INSERT INTO hub_results(
      result_id, scenario_name, run_id, package_id, title,
      best_score, best_elo, payload_path, tags_json, metadata_json, created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(result_id) DO UPDATE SET
      scenario_name = excluded.scenario_name,
      run_id = excluded.run_id,
      package_id = excluded.package_id,
      title = excluded.title,
      best_score = excluded.best_score,
      best_elo = excluded.best_elo,
      payload_path = excluded.payload_path,
      tags_json = excluded.tags_json,
      metadata_json = excluded.metadata_json,
      updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
  `).run(
    opts.resultId,
    opts.scenarioName,
    opts.runId,
    opts.packageId ?? null,
    opts.title,
    opts.bestScore,
    opts.bestElo,
    opts.payloadPath,
    JSON.stringify(opts.tags),
    JSON.stringify(opts.metadata ?? {}),
    opts.createdAt || nowIso(),
  );
}

export function getHubResultRecord(
  db: Database.Database,
  resultId: string,
): HubResultRecordRow | null {
  const row = db.prepare(
    "SELECT * FROM hub_results WHERE result_id = ?",
  ).get(resultId) as RawHubResultRecordRow | undefined;
  return row ? parseHubResultRow(row) : null;
}

export function listHubResultRecords(db: Database.Database): HubResultRecordRow[] {
  const rows = db.prepare(
    "SELECT * FROM hub_results ORDER BY created_at DESC",
  ).all() as RawHubResultRecordRow[];
  return rows.map((row) => parseHubResultRow(row));
}

export function saveHubPromotionRecord(
  db: Database.Database,
  opts: SaveHubPromotionRecordOpts,
): void {
  db.prepare(`
    INSERT INTO hub_promotions(
      event_id, package_id, source_run_id, action, actor, label, metadata_json, created_at
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(event_id) DO UPDATE SET
      package_id = excluded.package_id,
      source_run_id = excluded.source_run_id,
      action = excluded.action,
      actor = excluded.actor,
      label = excluded.label,
      metadata_json = excluded.metadata_json
  `).run(
    opts.eventId,
    opts.packageId,
    opts.sourceRunId,
    opts.action,
    opts.actor,
    opts.label ?? null,
    JSON.stringify(opts.metadata ?? {}),
    opts.createdAt || nowIso(),
  );
}

export function getHubPromotionRecord(
  db: Database.Database,
  eventId: string,
): HubPromotionRecordRow | null {
  const row = db.prepare(
    "SELECT * FROM hub_promotions WHERE event_id = ?",
  ).get(eventId) as RawHubPromotionRecordRow | undefined;
  return row ? parseHubPromotionRow(row) : null;
}

export function listHubPromotionRecords(db: Database.Database): HubPromotionRecordRow[] {
  const rows = db.prepare(
    "SELECT * FROM hub_promotions ORDER BY created_at DESC",
  ).all() as RawHubPromotionRecordRow[];
  return rows.map((row) => parseHubPromotionRow(row));
}
