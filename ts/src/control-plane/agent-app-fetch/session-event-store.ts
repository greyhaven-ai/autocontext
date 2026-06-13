export interface AgentAppFetchSessionEventRecord {
  eventId: string;
  sessionId: string;
  sequence: number;
  eventType: string;
  timestamp: string;
  payload: Record<string, unknown>;
  parentSessionId: string;
  taskId: string;
  workerId: string;
}

export interface AgentAppFetchSessionEventLogSnapshot {
  sessionId: string;
  parentSessionId: string;
  taskId: string;
  workerId: string;
  metadata: Record<string, unknown>;
  events: AgentAppFetchSessionEventRecord[];
  createdAt: string;
  updatedAt: string;
}

export interface AgentAppFetchSessionEventStoreAppendResult {
  appended: number;
  duplicateEventIds: string[];
  nextSequence: number;
}

export interface AgentAppFetchSessionEventStoreCapabilities {
  ordering: "per_session_sequence";
  idempotency: "event_id";
  consistency: "read_your_writes_after_append";
}

export interface AgentAppFetchSessionEventStore {
  readonly capabilities: AgentAppFetchSessionEventStoreCapabilities;
  append(
    snapshot: AgentAppFetchSessionEventLogSnapshot,
  ): MaybePromise<AgentAppFetchSessionEventStoreAppendResult>;
  load(sessionId: string): MaybePromise<AgentAppFetchSessionEventLogSnapshot | null>;
  list?(options?: { limit?: number }): MaybePromise<AgentAppFetchSessionEventLogSnapshot[]>;
  listChildren?(parentSessionId: string): MaybePromise<AgentAppFetchSessionEventLogSnapshot[]>;
  close?(): MaybePromise<void>;
}

export interface AgentAppFetchRuntimeSessionEventLogLike {
  toJSON(): AgentAppFetchSessionEventLogSnapshot | Record<string, unknown>;
}

export type AgentAppFetchRuntimeSessionEventLogSubscriber = (
  event: AgentAppFetchSessionEventRecord,
  log: AgentAppFetchRuntimeSessionReadableEventLog,
) => void;

export interface AgentAppFetchRuntimeSessionReadableEventLog extends AgentAppFetchRuntimeSessionEventLogLike {
  readonly sessionId: string;
  readonly parentSessionId: string;
  readonly taskId: string;
  readonly workerId: string;
  readonly events: AgentAppFetchSessionEventRecord[];
  readonly createdAt: string;
  updatedAt: string;
  metadata: Record<string, unknown>;
  append(eventType: string, payload?: Record<string, unknown>): AgentAppFetchSessionEventRecord;
  subscribe(callback: AgentAppFetchRuntimeSessionEventLogSubscriber): () => void;
}

export interface AgentAppFetchRuntimeSessionEventStoreAdapter {
  save(log: AgentAppFetchRuntimeSessionEventLogLike): void;
  load(sessionId: string): AgentAppFetchRuntimeSessionReadableEventLog | null;
  list(options?: { limit?: number }): AgentAppFetchRuntimeSessionReadableEventLog[];
  listChildren(parentSessionId: string): AgentAppFetchRuntimeSessionReadableEventLog[];
  close(): void;
}

export interface AgentAppFetchSessionEventStoreBridge {
  eventStore: AgentAppFetchRuntimeSessionEventStoreAdapter;
  flush(): Promise<void>;
}

type MaybePromise<T> = T | Promise<T>;

const AGENT_APP_FETCH_SESSION_EVENT_STORE_CAPABILITIES = {
  ordering: "per_session_sequence",
  idempotency: "event_id",
  consistency: "read_your_writes_after_append",
} as const satisfies AgentAppFetchSessionEventStoreCapabilities;

export function createAgentAppFetchSessionEventStoreBridge(
  store: AgentAppFetchSessionEventStore,
): AgentAppFetchSessionEventStoreBridge {
  const snapshots = new Map<string, AgentAppFetchSessionEventLogSnapshot>();
  const pending = new Map<string, AgentAppFetchSessionEventLogSnapshot>();
  const updateSnapshot = (snapshot: AgentAppFetchSessionEventLogSnapshot) => {
    const normalized = normalizeSnapshot(snapshot);
    snapshots.set(normalized.sessionId, normalized);
    pending.set(normalized.sessionId, normalized);
  };
  const readSnapshot = (sessionId: string) => snapshots.get(sessionId);
  const toBridgeLog = (snapshot: AgentAppFetchSessionEventLogSnapshot | undefined) =>
    snapshot ? new BridgeRuntimeSessionEventLog(snapshot, updateSnapshot) : null;

  return {
    eventStore: {
      save(log) {
        updateSnapshot(normalizeSnapshot(log.toJSON()));
      },
      load(sessionId) {
        return toBridgeLog(readSnapshot(sessionId));
      },
      list(options = {}) {
        const limit = positiveLimit(options.limit);
        return [...snapshots.values()]
          .sort((left, right) => compareSessionsForList(left, right))
          .slice(0, limit)
          .map((snapshot) => new BridgeRuntimeSessionEventLog(snapshot, updateSnapshot));
      },
      listChildren(parentSessionId) {
        return [...snapshots.values()]
          .filter((snapshot) => snapshot.parentSessionId === parentSessionId)
          .sort(compareChildSessionsForList)
          .map((snapshot) => new BridgeRuntimeSessionEventLog(snapshot, updateSnapshot));
      },
      close() {},
    },
    async flush() {
      const flushSnapshots = [...pending.values()].sort((left, right) =>
        left.sessionId.localeCompare(right.sessionId),
      );
      for (const snapshot of flushSnapshots) {
        await store.append(snapshot);
        pending.delete(snapshot.sessionId);
      }
    },
  };
}

export function createInMemoryAgentAppFetchSessionEventStore(): AgentAppFetchSessionEventStore {
  return new InMemoryAgentAppFetchSessionEventStore();
}

class BridgeRuntimeSessionEventLog implements AgentAppFetchRuntimeSessionReadableEventLog {
  readonly sessionId: string;
  readonly parentSessionId: string;
  readonly taskId: string;
  readonly workerId: string;
  readonly events: AgentAppFetchSessionEventRecord[];
  readonly createdAt: string;
  updatedAt: string;
  metadata: Record<string, unknown>;
  readonly #onChange: (snapshot: AgentAppFetchSessionEventLogSnapshot) => void;
  readonly #subscribers: AgentAppFetchRuntimeSessionEventLogSubscriber[] = [];

  constructor(
    snapshot: AgentAppFetchSessionEventLogSnapshot,
    onChange: (snapshot: AgentAppFetchSessionEventLogSnapshot) => void,
  ) {
    const clone = cloneSnapshot(snapshot);
    this.sessionId = clone.sessionId;
    this.parentSessionId = clone.parentSessionId;
    this.taskId = clone.taskId;
    this.workerId = clone.workerId;
    this.metadata = clone.metadata;
    this.events = clone.events;
    this.createdAt = clone.createdAt;
    this.updatedAt = clone.updatedAt;
    this.#onChange = onChange;
  }

  append(
    eventType: string,
    payload: Record<string, unknown> = {},
  ): AgentAppFetchSessionEventRecord {
    const event: AgentAppFetchSessionEventRecord = {
      eventId: createEdgeSafeEventId(),
      sessionId: this.sessionId,
      sequence: this.events.length,
      eventType,
      timestamp: new Date().toISOString(),
      payload: cloneJsonRecord(payload),
      parentSessionId: this.parentSessionId,
      taskId: this.taskId,
      workerId: this.workerId,
    };
    this.events.push(event);
    this.updatedAt = event.timestamp;
    this.#onChange(this.toJSON());
    for (const subscriber of [...this.#subscribers]) {
      subscriber(event, this);
    }
    return event;
  }

  subscribe(callback: AgentAppFetchRuntimeSessionEventLogSubscriber): () => void {
    this.#subscribers.push(callback);
    return () => {
      const index = this.#subscribers.indexOf(callback);
      if (index !== -1) this.#subscribers.splice(index, 1);
    };
  }

  toJSON(): AgentAppFetchSessionEventLogSnapshot {
    return cloneSnapshot({
      sessionId: this.sessionId,
      parentSessionId: this.parentSessionId,
      taskId: this.taskId,
      workerId: this.workerId,
      metadata: this.metadata,
      events: this.events,
      createdAt: this.createdAt,
      updatedAt: this.updatedAt,
    });
  }
}

class InMemoryAgentAppFetchSessionEventStore implements AgentAppFetchSessionEventStore {
  readonly capabilities = AGENT_APP_FETCH_SESSION_EVENT_STORE_CAPABILITIES;
  readonly #snapshots = new Map<string, AgentAppFetchSessionEventLogSnapshot>();

  async append(
    snapshot: AgentAppFetchSessionEventLogSnapshot,
  ): Promise<AgentAppFetchSessionEventStoreAppendResult> {
    const incoming = normalizeSnapshot(snapshot);
    const existing = this.#snapshots.get(incoming.sessionId);
    const merged = existing
      ? mergeSnapshot(existing, incoming)
      : normalizeSnapshot({ ...incoming, events: [] });
    const eventIds = new Set(merged.events.map((event) => event.eventId));
    const usedSequences = new Set(merged.events.map((event) => event.sequence));
    const duplicateEventIds: string[] = [];
    let appended = 0;
    let nextSequence = nextOpenSequence(usedSequences);

    for (const incomingEvent of incoming.events) {
      const event = normalizeEvent(incomingEvent, incoming.sessionId);
      if (eventIds.has(event.eventId)) {
        duplicateEventIds.push(event.eventId);
        continue;
      }
      if (usedSequences.has(event.sequence)) {
        event.sequence = nextSequence;
      }
      merged.events.push(event);
      eventIds.add(event.eventId);
      usedSequences.add(event.sequence);
      appended += 1;
      nextSequence = nextOpenSequence(usedSequences, event.sequence + 1);
    }

    merged.events.sort(compareEvents);
    merged.updatedAt = maxTimestamp(merged.updatedAt, incoming.updatedAt);
    this.#snapshots.set(merged.sessionId, cloneSnapshot(merged));
    return { appended, duplicateEventIds, nextSequence };
  }

  async load(sessionId: string): Promise<AgentAppFetchSessionEventLogSnapshot | null> {
    const snapshot = this.#snapshots.get(sessionId);
    return snapshot ? cloneSnapshot(snapshot) : null;
  }

  async list(options: { limit?: number } = {}): Promise<AgentAppFetchSessionEventLogSnapshot[]> {
    const limit = positiveLimit(options.limit);
    return [...this.#snapshots.values()]
      .sort((left, right) => compareSessionsForList(left, right))
      .slice(0, limit)
      .map(cloneSnapshot);
  }

  async listChildren(parentSessionId: string): Promise<AgentAppFetchSessionEventLogSnapshot[]> {
    return [...this.#snapshots.values()]
      .filter((snapshot) => snapshot.parentSessionId === parentSessionId)
      .sort(compareChildSessionsForList)
      .map(cloneSnapshot);
  }

  close(): void {
    this.#snapshots.clear();
  }
}

function normalizeSnapshot(
  value: AgentAppFetchSessionEventLogSnapshot | Record<string, unknown>,
): AgentAppFetchSessionEventLogSnapshot {
  const sessionId = readString(value.sessionId);
  if (!sessionId) throw new Error("Agent app Fetch session event snapshots require sessionId");
  const parentSessionId = readString(value.parentSessionId);
  const taskId = readString(value.taskId);
  const workerId = readString(value.workerId);
  const events = Array.isArray(value.events)
    ? value.events.filter(isRecord).map((event) => normalizeEvent(event, sessionId))
    : [];
  const createdAt = readString(value.createdAt) || firstTimestamp(events);
  const updatedAt = readString(value.updatedAt) || lastTimestamp(events) || createdAt;
  return {
    sessionId,
    parentSessionId,
    taskId,
    workerId,
    metadata: cloneJsonRecord(value.metadata),
    events: events.sort(compareEvents),
    createdAt,
    updatedAt,
  };
}

function mergeSnapshot(
  existing: AgentAppFetchSessionEventLogSnapshot,
  incoming: AgentAppFetchSessionEventLogSnapshot,
): AgentAppFetchSessionEventLogSnapshot {
  return {
    sessionId: existing.sessionId,
    parentSessionId: incoming.parentSessionId || existing.parentSessionId,
    taskId: incoming.taskId || existing.taskId,
    workerId: incoming.workerId || existing.workerId,
    metadata: cloneJsonRecord({ ...existing.metadata, ...incoming.metadata }),
    events: existing.events.map(cloneEvent),
    createdAt: existing.createdAt || incoming.createdAt,
    updatedAt: maxTimestamp(existing.updatedAt, incoming.updatedAt),
  };
}

function normalizeEvent(
  value: AgentAppFetchSessionEventRecord | Record<string, unknown>,
  fallbackSessionId: string,
): AgentAppFetchSessionEventRecord {
  const eventId = readString(value.eventId);
  if (!eventId) throw new Error("Agent app Fetch session events require eventId");
  return {
    eventId,
    sessionId: readString(value.sessionId) || fallbackSessionId,
    sequence: readNumber(value.sequence),
    eventType: readString(value.eventType),
    timestamp: readString(value.timestamp),
    payload: cloneJsonRecord(value.payload),
    parentSessionId: readString(value.parentSessionId),
    taskId: readString(value.taskId),
    workerId: readString(value.workerId),
  };
}

function cloneSnapshot(
  snapshot: AgentAppFetchSessionEventLogSnapshot,
): AgentAppFetchSessionEventLogSnapshot {
  return {
    ...snapshot,
    metadata: cloneJsonRecord(snapshot.metadata),
    events: snapshot.events.map(cloneEvent),
  };
}

function cloneEvent(event: AgentAppFetchSessionEventRecord): AgentAppFetchSessionEventRecord {
  return {
    ...event,
    payload: cloneJsonRecord(event.payload),
  };
}

function cloneJsonRecord(value: unknown): Record<string, unknown> {
  if (!isRecord(value)) return {};
  return cloneJsonValue(value) as Record<string, unknown>;
}

function cloneJsonValue(value: unknown): unknown {
  if (value === null) return null;
  if (typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (Array.isArray(value)) {
    return value.map((item) => {
      const cloned = cloneJsonValue(item);
      return cloned === undefined ? null : cloned;
    });
  }
  if (isRecord(value)) {
    const record: Record<string, unknown> = {};
    for (const [key, entry] of Object.entries(value)) {
      const cloned = cloneJsonValue(entry);
      if (cloned !== undefined) record[key] = cloned;
    }
    return record;
  }
  return undefined;
}

function readString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function readNumber(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function firstTimestamp(events: readonly AgentAppFetchSessionEventRecord[]): string {
  return events[0]?.timestamp ?? new Date(0).toISOString();
}

function lastTimestamp(events: readonly AgentAppFetchSessionEventRecord[]): string {
  return events.at(-1)?.timestamp ?? "";
}

function maxTimestamp(left: string, right: string): string {
  if (!left) return right;
  if (!right) return left;
  return left > right ? left : right;
}

function compareEvents(
  left: AgentAppFetchSessionEventRecord,
  right: AgentAppFetchSessionEventRecord,
): number {
  return (
    left.sequence - right.sequence ||
    left.timestamp.localeCompare(right.timestamp) ||
    left.eventId.localeCompare(right.eventId)
  );
}

function compareChildSessionsForList(
  left: AgentAppFetchSessionEventLogSnapshot,
  right: AgentAppFetchSessionEventLogSnapshot,
): number {
  return (
    left.createdAt.localeCompare(right.createdAt) || left.sessionId.localeCompare(right.sessionId)
  );
}

function compareSessionsForList(
  left: AgentAppFetchSessionEventLogSnapshot,
  right: AgentAppFetchSessionEventLogSnapshot,
): number {
  const leftTime = left.updatedAt || left.createdAt;
  const rightTime = right.updatedAt || right.createdAt;
  return rightTime.localeCompare(leftTime) || left.sessionId.localeCompare(right.sessionId);
}

function positiveLimit(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : 50;
}

function nextOpenSequence(usedSequences: ReadonlySet<number>, start = 0): number {
  let sequence = start;
  while (usedSequences.has(sequence)) {
    sequence += 1;
  }
  return sequence;
}

function createEdgeSafeEventId(): string {
  const randomUUID = globalThis.crypto?.randomUUID?.();
  if (randomUUID) return randomUUID.slice(0, 12);
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
}
