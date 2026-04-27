import type Database from "better-sqlite3";

import type { ConsultationRow, InsertConsultationOpts } from "./storage-contracts.js";
import {
  insertConsultationRecord,
  listConsultationRecords,
  totalConsultationCostRecord,
} from "./consultation-store.js";

export function insertStoreConsultation(
  db: Database.Database,
  opts: InsertConsultationOpts,
): number {
  return insertConsultationRecord(db, opts);
}

export function listStoreConsultations(
  db: Database.Database,
  runId: string,
): ConsultationRow[] {
  return listConsultationRecords(db, runId);
}

export function getStoreTotalConsultationCost(
  db: Database.Database,
  runId: string,
): number {
  return totalConsultationCostRecord(db, runId);
}
