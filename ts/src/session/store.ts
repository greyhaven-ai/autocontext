/**
 * Session persistence store — SQLite-backed (AC-507 TS parity).
 */

import Database from "better-sqlite3";
import { Session } from "./types.js";

export class SessionStore {
  private db: Database.Database;

  constructor(dbPath: string) {
    this.db = new Database(dbPath);
    this.db.pragma("journal_mode = WAL");
    this.ensureSchema();
  }

  save(session: Session): void {
    const data = JSON.stringify(session.toJSON());
    this.db.prepare(`
      INSERT INTO sessions (session_id, goal, status, data_json, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(session_id) DO UPDATE SET
        status = excluded.status,
        data_json = excluded.data_json,
        updated_at = excluded.updated_at
    `).run(session.sessionId, session.goal, session.status, data, session.createdAt, session.updatedAt);
  }

  load(sessionId: string): Session | null {
    const row = this.db.prepare("SELECT data_json FROM sessions WHERE session_id = ?").get(sessionId) as { data_json: string } | undefined;
    if (!row) return null;
    return Session.fromJSON(JSON.parse(row.data_json));
  }

  list(status?: string, limit = 50): Session[] {
    let query = "SELECT data_json FROM sessions";
    const params: (string | number)[] = [];
    if (status) { query += " WHERE status = ?"; params.push(status); }
    query += " ORDER BY created_at DESC LIMIT ?";
    params.push(limit);
    const rows = this.db.prepare(query).all(...params) as { data_json: string }[];
    return rows.map((r) => Session.fromJSON(JSON.parse(r.data_json)));
  }

  delete(sessionId: string): boolean {
    const result = this.db.prepare("DELETE FROM sessions WHERE session_id = ?").run(sessionId);
    return result.changes > 0;
  }

  close(): void { this.db.close(); }

  private ensureSchema(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        goal TEXT NOT NULL,
        status TEXT NOT NULL,
        data_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT ''
      )
    `);
  }
}
