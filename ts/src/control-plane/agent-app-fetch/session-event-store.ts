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

export interface AgentAppFetchRuntimeSessionEventStoreAdapter {
  save(log: AgentAppFetchRuntimeSessionEventLogLike): void;
  load(sessionId: string): null;
  list(options?: { limit?: number }): [];
  listChildren(parentSessionId: string): [];
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
  const pending = new Map<string, AgentAppFetchSessionEventLogSnapshot>();
  return {
    eventStore: {
      save(log) {
        const snapshot = normalizeSnapshot(log.toJSON());
        pending.set(snapshot.sessionId, snapshot);
      },
      load() {
        return null;
      },
      list() {
        return [];
      },
      listChildren() {
        return [];
      },
      close() {},
    },
    async flush() {
      const snapshots = [...pending.values()].sort((left, right) =>
        left.sessionId.localeCompare(right.sessionId),
      );
      for (const snapshot of snapshots) {
        await store.append(snapshot);
        pending.delete(snapshot.sessionId);
      }
    },
  };
}

export function createInMemoryAgentAppFetchSessionEventStore(): AgentAppFetchSessionEventStore {
  return new InMemoryAgentAppFetchSessionEventStore();
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
      .sort(
        (left, right) =>
          left.createdAt.localeCompare(right.createdAt) ||
          left.sessionId.localeCompare(right.sessionId),
      )
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
    metadata: cloneRecord(value.metadata),
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
    metadata: { ...existing.metadata, ...incoming.metadata },
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
    payload: cloneRecord(value.payload),
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
    metadata: { ...snapshot.metadata },
    events: snapshot.events.map(cloneEvent),
  };
}

function cloneEvent(event: AgentAppFetchSessionEventRecord): AgentAppFetchSessionEventRecord {
  return {
    ...event,
    payload: { ...event.payload },
  };
}

function cloneRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? { ...value } : {};
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
