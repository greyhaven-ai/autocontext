import type Database from "better-sqlite3";

import type { NotebookRow, UpsertNotebookOpts } from "./storage-contracts.js";
import {
  deleteNotebookRecord,
  getNotebookRecord,
  listNotebookRecords,
  upsertNotebookRecord,
} from "./notebook-store.js";

export function upsertStoreNotebook(
  db: Database.Database,
  opts: UpsertNotebookOpts,
): void {
  upsertNotebookRecord(db, opts);
}

export function getStoreNotebook(
  db: Database.Database,
  sessionId: string,
): NotebookRow | null {
  return getNotebookRecord(db, sessionId);
}

export function listStoreNotebooks(db: Database.Database): NotebookRow[] {
  return listNotebookRecords(db);
}

export function deleteStoreNotebook(
  db: Database.Database,
  sessionId: string,
): boolean {
  return deleteNotebookRecord(db, sessionId);
}
