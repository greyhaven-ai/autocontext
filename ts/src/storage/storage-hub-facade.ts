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
import {
  getHubPackageRecord,
  getHubPromotionRecord,
  getHubResultRecord,
  getHubSessionRecord,
  heartbeatHubSessionRecord,
  listHubPackageRecords,
  listHubPromotionRecords,
  listHubResultRecords,
  listHubSessionRecords,
  saveHubPackageRecord,
  saveHubPromotionRecord,
  saveHubResultRecord,
  upsertHubSessionRecord,
} from "./hub-store.js";

export function upsertStoreHubSession(
  db: Database.Database,
  sessionId: string,
  opts: UpsertHubSessionOpts,
): void {
  upsertHubSessionRecord(db, sessionId, opts);
}

export function heartbeatStoreHubSession(
  db: Database.Database,
  sessionId: string,
  opts: { lastHeartbeatAt: string; leaseExpiresAt?: string | null },
): void {
  heartbeatHubSessionRecord(db, sessionId, opts);
}

export function getStoreHubSession(
  db: Database.Database,
  sessionId: string,
): HubSessionRow | null {
  return getHubSessionRecord(db, sessionId);
}

export function listStoreHubSessions(db: Database.Database): HubSessionRow[] {
  return listHubSessionRecords(db);
}

export function saveStoreHubPackageRecord(
  db: Database.Database,
  opts: SaveHubPackageRecordOpts,
): void {
  saveHubPackageRecord(db, opts);
}

export function getStoreHubPackageRecord(
  db: Database.Database,
  packageId: string,
): HubPackageRecordRow | null {
  return getHubPackageRecord(db, packageId);
}

export function listStoreHubPackageRecords(db: Database.Database): HubPackageRecordRow[] {
  return listHubPackageRecords(db);
}

export function saveStoreHubResultRecord(
  db: Database.Database,
  opts: SaveHubResultRecordOpts,
): void {
  saveHubResultRecord(db, opts);
}

export function getStoreHubResultRecord(
  db: Database.Database,
  resultId: string,
): HubResultRecordRow | null {
  return getHubResultRecord(db, resultId);
}

export function listStoreHubResultRecords(db: Database.Database): HubResultRecordRow[] {
  return listHubResultRecords(db);
}

export function saveStoreHubPromotionRecord(
  db: Database.Database,
  opts: SaveHubPromotionRecordOpts,
): void {
  saveHubPromotionRecord(db, opts);
}

export function getStoreHubPromotionRecord(
  db: Database.Database,
  eventId: string,
): HubPromotionRecordRow | null {
  return getHubPromotionRecord(db, eventId);
}

export function listStoreHubPromotionRecords(db: Database.Database): HubPromotionRecordRow[] {
  return listHubPromotionRecords(db);
}
