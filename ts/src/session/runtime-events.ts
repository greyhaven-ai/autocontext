import { randomUUID } from "node:crypto";
import Database from "better-sqlite3";

export const RuntimeSessionEventType = {
  PROMPT_SUBMITTED: "prompt_submitted",
  ASSISTANT_MESSAGE: "assistant_message",
  SHELL_COMMAND: "shell_command",
  TOOL_CALL: "tool_call",
  CHILD_TASK_STARTED: "child_task_started",
  CHILD_TASK_COMPLETED: "child_task_completed",
  COMPACTION: "compaction",
} as const;
export type RuntimeSessionEventType =
  (typeof RuntimeSessionEventType)[keyof typeof RuntimeSessionEventType];

export interface RuntimeSessionEvent {
  readonly eventId: string;
  readonly sessionId: string;
  readonly sequence: number;
  readonly eventType: RuntimeSessionEventType;
  readonly timestamp: string;
  readonly payload: Record<string, unknown>;
  readonly parentSessionId: string;
  readonly taskId: string;
  readonly workerId: string;
}

export interface RuntimeSessionEventLogCreateOpts {
  sessionId: string;
  parentSessionId?: string;
  taskId?: string;
  workerId?: string;
  metadata?: Record<string, unknown>;
}

export interface RuntimeSessionEventLogJSON {
  sessionId: string;
  parentSessionId: string;
  taskId: string;
  workerId: string;
  metadata: Record<string, unknown>;
  events: RuntimeSessionEvent[];
  createdAt: string;
  updatedAt: string;
}

export type RuntimeSessionEventLogSubscriber = (
  event: RuntimeSessionEvent,
  log: RuntimeSessionEventLog,
) => void;

function nowIso(): string {
  return new Date().toISOString();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function readString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function readNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" ? value : fallback;
}

function readEventType(value: unknown): RuntimeSessionEventType {
  switch (value) {
    case RuntimeSessionEventType.PROMPT_SUBMITTED:
      return RuntimeSessionEventType.PROMPT_SUBMITTED;
    case RuntimeSessionEventType.ASSISTANT_MESSAGE:
      return RuntimeSessionEventType.ASSISTANT_MESSAGE;
    case RuntimeSessionEventType.SHELL_COMMAND:
      return RuntimeSessionEventType.SHELL_COMMAND;
    case RuntimeSessionEventType.TOOL_CALL:
      return RuntimeSessionEventType.TOOL_CALL;
    case RuntimeSessionEventType.CHILD_TASK_STARTED:
      return RuntimeSessionEventType.CHILD_TASK_STARTED;
    case RuntimeSessionEventType.CHILD_TASK_COMPLETED:
      return RuntimeSessionEventType.CHILD_TASK_COMPLETED;
    case RuntimeSessionEventType.COMPACTION:
      return RuntimeSessionEventType.COMPACTION;
    default:
      throw new Error(`Unknown runtime session event type: ${String(value)}`);
  }
}

function eventFromRecord(value: Record<string, unknown>): RuntimeSessionEvent {
  return {
    eventId: readString(value.eventId) || randomUUID().slice(0, 12),
    sessionId: readString(value.sessionId),
    sequence: readNumber(value.sequence),
    eventType: readEventType(value.eventType),
    timestamp: readString(value.timestamp) || nowIso(),
    payload: readRecord(value.payload),
    parentSessionId: readString(value.parentSessionId),
    taskId: readString(value.taskId),
    workerId: readString(value.workerId),
  };
}

export class RuntimeSessionEventLog {
  readonly sessionId: string;
  readonly parentSessionId: string;
  readonly taskId: string;
  readonly workerId: string;
  readonly metadata: Record<string, unknown>;
  readonly events: RuntimeSessionEvent[] = [];
  readonly createdAt: string;
  updatedAt: string;
  private readonly subscribers: RuntimeSessionEventLogSubscriber[] = [];

  private constructor(opts: RuntimeSessionEventLogCreateOpts & { createdAt?: string; updatedAt?: string }) {
    this.sessionId = opts.sessionId;
    this.parentSessionId = opts.parentSessionId ?? "";
    this.taskId = opts.taskId ?? "";
    this.workerId = opts.workerId ?? "";
    this.metadata = opts.metadata ?? {};
    this.createdAt = opts.createdAt ?? nowIso();
    this.updatedAt = opts.updatedAt ?? "";
  }

  static create(opts: RuntimeSessionEventLogCreateOpts): RuntimeSessionEventLog {
    return new RuntimeSessionEventLog(opts);
  }

  append(
    eventType: RuntimeSessionEventType,
    payload: Record<string, unknown> = {},
  ): RuntimeSessionEvent {
    const event: RuntimeSessionEvent = {
      eventId: randomUUID().slice(0, 12),
      sessionId: this.sessionId,
      sequence: this.events.length,
      eventType,
      timestamp: nowIso(),
      payload,
      parentSessionId: this.parentSessionId,
      taskId: this.taskId,
      workerId: this.workerId,
    };
    this.events.push(event);
    this.updatedAt = event.timestamp;
    this.notify(event);
    return event;
  }

  subscribe(callback: RuntimeSessionEventLogSubscriber): () => void {
    this.subscribers.push(callback);
    return () => {
      const idx = this.subscribers.indexOf(callback);
      if (idx !== -1) {
        this.subscribers.splice(idx, 1);
      }
    };
  }

  toJSON(): RuntimeSessionEventLogJSON {
    return {
      sessionId: this.sessionId,
      parentSessionId: this.parentSessionId,
      taskId: this.taskId,
      workerId: this.workerId,
      metadata: this.metadata,
      events: this.events.map((event) => ({ ...event, payload: { ...event.payload } })),
      createdAt: this.createdAt,
      updatedAt: this.updatedAt,
    };
  }

  static fromJSON(data: RuntimeSessionEventLogJSON | Record<string, unknown>): RuntimeSessionEventLog {
    const log = new RuntimeSessionEventLog({
      sessionId: readString(data.sessionId),
      parentSessionId: readString(data.parentSessionId),
      taskId: readString(data.taskId),
      workerId: readString(data.workerId),
      metadata: readRecord(data.metadata),
      createdAt: readString(data.createdAt) || nowIso(),
      updatedAt: readString(data.updatedAt),
    });
    const eventRecords = Array.isArray(data.events)
      ? data.events.filter(isRecord)
      : [];
    for (const eventRecord of eventRecords) {
      log.events.push(eventFromRecord(eventRecord));
    }
    return log;
  }

  private notify(event: RuntimeSessionEvent): void {
    const subscribers = [...this.subscribers];
    for (const subscriber of subscribers) {
      subscriber(event, this);
    }
  }
}

export class RuntimeSessionEventStore {
  private readonly db: Database.Database;

  constructor(dbPath: string) {
    this.db = new Database(dbPath);
    this.db.pragma("journal_mode = WAL");
    this.ensureSchema();
  }

  save(log: RuntimeSessionEventLog): void {
    const data = log.toJSON();
    const transaction = this.db.transaction(() => {
      this.db.prepare(`
        INSERT INTO runtime_sessions (
          session_id, parent_session_id, task_id, worker_id, metadata_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
          parent_session_id = excluded.parent_session_id,
          task_id = excluded.task_id,
          worker_id = excluded.worker_id,
          metadata_json = excluded.metadata_json,
          updated_at = CASE
            WHEN excluded.updated_at > runtime_sessions.updated_at THEN excluded.updated_at
            ELSE runtime_sessions.updated_at
          END
      `).run(
        data.sessionId,
        data.parentSessionId,
        data.taskId,
        data.workerId,
        JSON.stringify(data.metadata),
        data.createdAt,
        data.updatedAt,
      );
      const existingRows = this.db.prepare(`
        SELECT event_id, sequence
        FROM runtime_session_events
        WHERE session_id = ?
      `).all(data.sessionId) as RuntimeSessionEventKeyRow[];
      const existingEventIds = new Set(existingRows.map((row) => row.event_id));
      const usedSequences = new Set(existingRows.map((row) => row.sequence));
      let nextSequence = nextRuntimeSessionSequence(usedSequences);
      const insertEvent = this.db.prepare(`
        INSERT OR IGNORE INTO runtime_session_events (
          event_id, session_id, sequence, event_type, timestamp,
          parent_session_id, task_id, worker_id, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      `);
      for (const event of data.events) {
        if (existingEventIds.has(event.eventId)) continue;
        let sequence = Number.isInteger(event.sequence) && event.sequence >= 0
          ? event.sequence
          : nextSequence;
        if (usedSequences.has(sequence)) {
          sequence = nextSequence;
        }
        insertEvent.run(
          event.eventId,
          event.sessionId || data.sessionId,
          sequence,
          event.eventType,
          event.timestamp,
          event.parentSessionId,
          event.taskId,
          event.workerId,
          JSON.stringify(event.payload),
        );
        existingEventIds.add(event.eventId);
        usedSequences.add(sequence);
        nextSequence = nextRuntimeSessionSequence(usedSequences, sequence + 1);
      }
    });
    transaction();
  }

  load(sessionId: string): RuntimeSessionEventLog | null {
    const session = this.db.prepare(`
      SELECT session_id, parent_session_id, task_id, worker_id, metadata_json, created_at, updated_at
      FROM runtime_sessions
      WHERE session_id = ?
    `).get(sessionId) as RuntimeSessionRow | undefined;
    if (!session) return null;
    const events = this.db.prepare(`
      SELECT event_id, session_id, sequence, event_type, timestamp,
             parent_session_id, task_id, worker_id, payload_json
      FROM runtime_session_events
      WHERE session_id = ?
      ORDER BY sequence ASC
    `).all(sessionId) as RuntimeSessionEventRow[];
    return RuntimeSessionEventLog.fromJSON({
      sessionId: session.session_id,
      parentSessionId: session.parent_session_id,
      taskId: session.task_id,
      workerId: session.worker_id,
      metadata: safeJsonRecord(session.metadata_json),
      createdAt: session.created_at,
      updatedAt: session.updated_at,
      events: events.map(rowToEvent),
    });
  }

  list(opts: { limit?: number } = {}): RuntimeSessionEventLog[] {
    const limit = Number.isInteger(opts.limit) && (opts.limit ?? 0) > 0 ? opts.limit! : 50;
    const rows = this.db.prepare(`
      SELECT session_id
      FROM runtime_sessions
      ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC, created_at DESC, session_id ASC
      LIMIT ?
    `).all(limit) as Array<{ session_id: string }>;
    return rows
      .map((row) => this.load(row.session_id))
      .filter((log): log is RuntimeSessionEventLog => log !== null);
  }

  listChildren(parentSessionId: string): RuntimeSessionEventLog[] {
    const rows = this.db.prepare(`
      SELECT session_id
      FROM runtime_sessions
      WHERE parent_session_id = ?
      ORDER BY created_at ASC, session_id ASC
    `).all(parentSessionId) as Array<{ session_id: string }>;
    return rows
      .map((row) => this.load(row.session_id))
      .filter((log): log is RuntimeSessionEventLog => log !== null);
  }

  close(): void {
    this.db.close();
  }

  private ensureSchema(): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS runtime_sessions (
        session_id TEXT PRIMARY KEY,
        parent_session_id TEXT NOT NULL DEFAULT '',
        task_id TEXT NOT NULL DEFAULT '',
        worker_id TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL DEFAULT ''
      );

      CREATE TABLE IF NOT EXISTS runtime_session_events (
        event_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        event_type TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        parent_session_id TEXT NOT NULL DEFAULT '',
        task_id TEXT NOT NULL DEFAULT '',
        worker_id TEXT NOT NULL DEFAULT '',
        payload_json TEXT NOT NULL,
        UNIQUE(session_id, sequence)
      );

      CREATE INDEX IF NOT EXISTS idx_runtime_sessions_parent
      ON runtime_sessions(parent_session_id);

      CREATE INDEX IF NOT EXISTS idx_runtime_sessions_updated
      ON runtime_sessions(updated_at, created_at);

      CREATE INDEX IF NOT EXISTS idx_runtime_session_events_session
      ON runtime_session_events(session_id, sequence);
    `);
  }
}

type RuntimeSessionRow = {
  session_id: string;
  parent_session_id: string;
  task_id: string;
  worker_id: string;
  metadata_json: string;
  created_at: string;
  updated_at: string;
};

type RuntimeSessionEventRow = {
  event_id: string;
  session_id: string;
  sequence: number;
  event_type: string;
  timestamp: string;
  parent_session_id: string;
  task_id: string;
  worker_id: string;
  payload_json: string;
};

type RuntimeSessionEventKeyRow = {
  event_id: string;
  sequence: number;
};

function nextRuntimeSessionSequence(
  usedSequences: Set<number>,
  start = usedSequences.size,
): number {
  let sequence = start;
  while (usedSequences.has(sequence)) {
    sequence += 1;
  }
  return sequence;
}

function rowToEvent(row: RuntimeSessionEventRow): RuntimeSessionEvent {
  return {
    eventId: row.event_id,
    sessionId: row.session_id,
    sequence: row.sequence,
    eventType: readEventType(row.event_type),
    timestamp: row.timestamp,
    payload: safeJsonRecord(row.payload_json),
    parentSessionId: row.parent_session_id,
    taskId: row.task_id,
    workerId: row.worker_id,
  };
}

function safeJsonRecord(json: string): Record<string, unknown> {
  try {
    return readRecord(JSON.parse(json));
  } catch {
    return {};
  }
}
