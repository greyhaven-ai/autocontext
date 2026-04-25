import type Database from "better-sqlite3";

import type {
  InsertMonitorAlertOpts,
  InsertMonitorConditionOpts,
  MonitorAlertRow,
  MonitorConditionRow,
} from "./storage-contracts.js";
import {
  countMonitorConditionRecords,
  deactivateMonitorConditionRecord,
  getLatestMonitorAlertRecord,
  getMonitorConditionRecord,
  insertMonitorAlertRecord,
  insertMonitorConditionRecord,
  listMonitorAlertRecords,
  listMonitorConditionRecords,
} from "./monitor-store.js";

export function insertStoreMonitorCondition(
  db: Database.Database,
  opts: InsertMonitorConditionOpts,
): string {
  return insertMonitorConditionRecord(db, opts);
}

export function listStoreMonitorConditions(
  db: Database.Database,
  opts?: { activeOnly?: boolean; scope?: string },
): MonitorConditionRow[] {
  return listMonitorConditionRecords(db, opts);
}

export function countStoreMonitorConditions(
  db: Database.Database,
  opts?: { activeOnly?: boolean; scope?: string },
): number {
  return countMonitorConditionRecords(db, opts);
}

export function getStoreMonitorCondition(
  db: Database.Database,
  conditionId: string,
): MonitorConditionRow | null {
  return getMonitorConditionRecord(db, conditionId);
}

export function deactivateStoreMonitorCondition(
  db: Database.Database,
  conditionId: string,
): boolean {
  return deactivateMonitorConditionRecord(db, conditionId);
}

export function insertStoreMonitorAlert(
  db: Database.Database,
  opts: InsertMonitorAlertOpts,
): string {
  return insertMonitorAlertRecord(db, opts);
}

export function listStoreMonitorAlerts(
  db: Database.Database,
  opts?: {
    conditionId?: string;
    scope?: string;
    limit?: number;
    since?: string;
  },
): MonitorAlertRow[] {
  return listMonitorAlertRecords(db, opts);
}

export function getStoreLatestMonitorAlert(
  db: Database.Database,
  conditionId: string,
): MonitorAlertRow | null {
  return getLatestMonitorAlertRecord(db, conditionId);
}
