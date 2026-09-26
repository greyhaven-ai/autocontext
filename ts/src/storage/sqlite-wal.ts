import Database from "better-sqlite3";

// better-sqlite3's default busy timeout, which every caller opens its database with.
const WAL_RETRY_TIMEOUT_MS = 5_000;
const WAL_RETRY_POLL_MS = 50;
const WAL_RETRY_WAIT_BUFFER = new Int32Array(new SharedArrayBuffer(4));
// In-memory databases cannot use WAL; SQLite keeps reporting "memory" for them.
const ACCEPTED_JOURNAL_MODES = new Set(["wal", "memory"]);

/**
 * Switch the database to WAL, tolerating a concurrent writer.
 *
 * Switching a fresh database to WAL upgrades a read lock to a write lock, and
 * SQLite reports contention there as SQLITE_BUSY without consulting the busy
 * handler, so the `timeout` database option does not cover it.
 */
export function enableWal(db: Pick<Database.Database, "pragma">): void {
  const deadline = Date.now() + WAL_RETRY_TIMEOUT_MS;
  for (;;) {
    let mode: string | undefined;
    try {
      mode = String(db.pragma("journal_mode = WAL", { simple: true })).toLowerCase();
    } catch (error: unknown) {
      if (!(error instanceof Database.SqliteError) || error.code !== "SQLITE_BUSY") {
        throw error;
      }
      if (Date.now() >= deadline) {
        throw error;
      }
    }
    if (mode !== undefined) {
      if (ACCEPTED_JOURNAL_MODES.has(mode)) {
        return;
      }
      if (Date.now() >= deadline) {
        throw new Error(`could not switch to WAL journal mode; database reports '${mode}'`);
      }
    }
    Atomics.wait(WAL_RETRY_WAIT_BUFFER, 0, 0, WAL_RETRY_POLL_MS);
  }
}
