import { createHash, randomUUID } from "node:crypto";
import {
  appendFileSync,
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname } from "node:path";
import { z } from "zod";

import {
  ServerMessageSchema,
  TRANSCRIPT_PROTOCOL_VERSION,
  type ClientMessage,
  type ServerMessage,
} from "./protocol.js";
import { sanitizeRunTranscriptMessage } from "./run-transcript-frame.js";

const RETAINED_MESSAGE_TYPES = new Set([
  "ack",
  "chat_response",
  "error",
  "event",
  "monitor_alert",
  "run_accepted",
  "state",
]);

const RetainedRunMessageSchema = ServerMessageSchema.and(
  z.object({
    client_run_id: z.string().min(1).max(200),
    event_id: z.string().min(1),
    sequence: z.number().int().positive(),
    occurred_at: z.string().datetime(),
  }),
);

export type RetainedRunMessage = z.infer<typeof RetainedRunMessageSchema>;

export interface RetainedRunFrame {
  clientRunId: string;
  eventId: string;
  message: RetainedRunMessage;
  occurredAt: string;
  runId: string | null;
  sequence: number;
  wire: string;
}

type CommandStatus = "completed" | "pending";

interface CommandRecord {
  clientRunId: string;
  commandId: string;
  fingerprint: string;
  occurredAt: string;
  responseEventId?: string;
  status: CommandStatus;
}

interface PersistedRunFrame {
  record_type: "frame";
  transcript_protocol_version: number;
  client_run_id: string;
  event_id: string;
  occurred_at: string;
  run_id?: string;
  sequence: number;
  wire: string;
}

interface PersistedCommandRecord {
  record_type: "command";
  transcript_protocol_version: number;
  client_run_id: string;
  command_id: string;
  fingerprint: string;
  occurred_at: string;
  response_event_id?: string;
  status: CommandStatus;
}

export type BeginCommandResult =
  | { outcome: "completed"; frame: RetainedRunFrame }
  | { outcome: "conflict" }
  | { outcome: "pending" }
  | { outcome: "proceed" };

export type ExistingCommandResult = Exclude<BeginCommandResult, { outcome: "proceed" }>;

export interface RunTranscriptRetentionPolicy {
  loadTailBytes: number;
  maxAgeMs: number;
  maxCommands: number;
  maxFileBytes: number;
  maxFrames: number;
  maxFramesPerRun: number;
  maxRecordBytes: number;
}

export const RUN_TRANSCRIPT_RETENTION: RunTranscriptRetentionPolicy = {
  loadTailBytes: 32 * 1_024 * 1_024,
  maxAgeMs: 7 * 24 * 60 * 60 * 1_000,
  maxCommands: 20_000,
  maxFileBytes: 32 * 1_024 * 1_024,
  maxFrames: 20_000,
  maxFramesPerRun: 2_000,
  maxRecordBytes: 32 * 1_024,
};

interface SerializedRecord {
  commandKey?: string;
  eventId?: string;
  line: string;
  occurredAt: string;
}

export class RunTranscriptStore {
  readonly path: string;
  readonly #policy: RunTranscriptRetentionPolicy;
  readonly #framesByClientRunId = new Map<string, RetainedRunFrame[]>();
  readonly #commands = new Map<string, CommandRecord>();
  readonly #clientRunIdByRunId = new Map<string, string>();
  readonly #runIdByClientRunId = new Map<string, string>();

  constructor(path: string, policy: Partial<RunTranscriptRetentionPolicy> = {}) {
    this.path = path;
    this.#policy = validatePolicy({ ...RUN_TRANSCRIPT_RETENTION, ...policy });
    const requiresCompaction = this.#loadBoundedTail();
    this.#enforceRetention(requiresCompaction);
  }

  record(opts: {
    clientRunId: string;
    commandId?: string;
    message: ServerMessage;
    occurredAt?: string;
    runId?: string | null;
  }): RetainedRunFrame | null {
    const commandId = opts.commandId ?? readCommandId(opts.message) ?? undefined;
    const safeMessage = sanitizeRunTranscriptMessage(opts.message);
    if (!safeMessage) return null;

    const existingRunId = this.#runIdByClientRunId.get(opts.clientRunId);
    const runId = opts.runId ?? existingRunId ?? readRunId(safeMessage);
    this.#assertScopeAvailable(opts.clientRunId, runId);

    const sequence = (this.latestSequence(opts.clientRunId) ?? 0) + 1;
    const eventId = randomUUID();
    const occurredAt = normalizeOccurredAt(opts.occurredAt);
    const message = RetainedRunMessageSchema.parse({
      ...safeMessage,
      ...(runId ? { run_id: runId } : {}),
      ...(commandId && supportsCommandId(safeMessage) ? { command_id: commandId } : {}),
      client_run_id: opts.clientRunId,
      event_id: eventId,
      sequence,
      occurred_at: occurredAt,
    });
    const wire = JSON.stringify(message);
    const frame: RetainedRunFrame = {
      clientRunId: opts.clientRunId,
      eventId,
      message,
      occurredAt,
      runId: runId ?? null,
      sequence,
      wire,
    };
    this.#appendLine(serializeFrame(frame));
    this.#addLoadedFrame(frame);
    this.#enforceRetention(false);
    return frame;
  }

  beginCommand(opts: {
    clientRunId: string;
    commandId: string;
    command: ClientMessage;
  }): BeginCommandResult {
    const existingResult = this.inspectCommand(opts);
    if (existingResult) return existingResult;

    const key = commandKey(opts.clientRunId, opts.commandId);
    const fingerprint = fingerprintCommand(opts.command);
    const record: CommandRecord = {
      clientRunId: opts.clientRunId,
      commandId: opts.commandId,
      fingerprint,
      occurredAt: new Date().toISOString(),
      status: "pending",
    };
    this.#appendLine(serializeCommand(record));
    this.#commands.set(key, record);
    this.#enforceRetention(false);
    return { outcome: "proceed" };
  }

  inspectCommand(opts: {
    clientRunId: string;
    commandId: string;
    command: ClientMessage;
  }): ExistingCommandResult | null {
    const existing = this.#commands.get(commandKey(opts.clientRunId, opts.commandId));
    if (!existing) return null;
    if (existing.fingerprint !== fingerprintCommand(opts.command)) {
      return { outcome: "conflict" };
    }
    if (existing.status === "completed" && existing.responseEventId) {
      const frame = this.#frameByEventId(existing.responseEventId);
      if (frame) return { outcome: "completed", frame };
    }
    return { outcome: "pending" };
  }

  canCompleteCommand(opts: {
    clientRunId: string;
    commandId: string;
    command: ClientMessage;
  }): boolean {
    const existing = this.#commands.get(commandKey(opts.clientRunId, opts.commandId));
    return (
      existing?.status === "pending" && existing.fingerprint === fingerprintCommand(opts.command)
    );
  }

  completeCommand(opts: {
    clientRunId: string;
    commandId: string;
    command: ClientMessage;
    frame: RetainedRunFrame;
  }): void {
    const key = commandKey(opts.clientRunId, opts.commandId);
    const existing = this.#commands.get(key);
    const fingerprint = fingerprintCommand(opts.command);
    if (existing?.status !== "pending" || existing.fingerprint !== fingerprint) {
      throw new Error("command outcome is not pending");
    }
    const completed: CommandRecord = {
      ...existing,
      occurredAt: new Date().toISOString(),
      responseEventId: opts.frame.eventId,
      status: "completed",
    };
    this.#appendLine(serializeCommand(completed));
    this.#commands.set(key, completed);
    this.#enforceRetention(false);
  }

  promoteStopCommandTerminalFrame(opts: {
    clientRunId: string;
    commandId: string;
    command: Extract<ClientMessage, { type: "stop" }>;
    frame: RetainedRunFrame;
  }): RetainedRunFrame {
    if (
      opts.command.client_run_id !== opts.clientRunId ||
      opts.command.command_id !== opts.commandId
    ) {
      throw new Error("stop command correlation does not match the requested command scope");
    }

    const key = commandKey(opts.clientRunId, opts.commandId);
    const existing = this.#commands.get(key);
    if (!existing || existing.fingerprint !== fingerprintCommand(opts.command)) {
      throw new Error("stop command fingerprint does not match the recorded request");
    }

    const retained = this.#frameByEventId(opts.frame.eventId);
    if (!retained || retained.wire !== opts.frame.wire) {
      throw new Error("run_stopped terminal frame is not retained by this transcript");
    }
    if (retained.clientRunId !== opts.clientRunId) {
      throw new Error("run_stopped terminal frame belongs to a different client run");
    }

    const expectedRunId = this.#runIdByClientRunId.get(opts.clientRunId);
    const payload = readRunStoppedPayload(retained.message);
    if (
      !expectedRunId ||
      retained.runId !== expectedRunId ||
      !payload ||
      payload.runId !== expectedRunId ||
      payload.commandId !== opts.commandId
    ) {
      throw new Error("run_stopped terminal frame does not match the stop command scope");
    }

    if (existing.status === "completed" && existing.responseEventId) {
      if (existing.responseEventId === retained.eventId) return retained;
      const existingResponse = this.#frameByEventId(existing.responseEventId);
      if (existingResponse && readRunStoppedPayload(existingResponse.message)) {
        throw new Error("stop command is already promoted to a different terminal frame");
      }
    }

    const completed: CommandRecord = {
      ...existing,
      occurredAt: new Date().toISOString(),
      responseEventId: retained.eventId,
      status: "completed",
    };
    this.#appendLine(serializeCommand(completed));
    this.#commands.set(key, completed);
    this.#enforceRetention(false);
    return retained;
  }

  framesAfter(clientRunId: string, afterSequence: number): RetainedRunFrame[] {
    return (this.#framesByClientRunId.get(clientRunId) ?? [])
      .filter((frame) => frame.sequence > afterSequence)
      .map((frame) => ({ ...frame, message: { ...frame.message } }));
  }

  latestSequence(clientRunId: string): number | null {
    return this.#framesByClientRunId.get(clientRunId)?.at(-1)?.sequence ?? null;
  }

  latestFrameOfType(clientRunId: string, type: ServerMessage["type"]): RetainedRunFrame | null {
    const frames = this.#framesByClientRunId.get(clientRunId) ?? [];
    return [...frames].reverse().find((frame) => frame.message.type === type) ?? null;
  }

  findCommandFrame(clientRunId: string, commandId: string): RetainedRunFrame | null {
    const frames = this.#framesByClientRunId.get(clientRunId) ?? [];
    return (
      [...frames].reverse().find((frame) => readCommandId(frame.message) === commandId) ?? null
    );
  }

  registerRun(clientRunId: string, runId: string): void {
    this.#assertScopeAvailable(clientRunId, runId);
    this.#runIdByClientRunId.set(clientRunId, runId);
    this.#clientRunIdByRunId.set(runId, clientRunId);
  }

  resolveClientRunId(runId: string): string | null {
    return this.#clientRunIdByRunId.get(runId) ?? null;
  }

  resolveRunId(clientRunId: string): string | null {
    return this.#runIdByClientRunId.get(clientRunId) ?? null;
  }

  hasFrames(clientRunId: string): boolean {
    return (this.#framesByClientRunId.get(clientRunId)?.length ?? 0) > 0;
  }

  #loadBoundedTail(): boolean {
    if (!existsSync(this.path)) return false;
    const size = statSync(this.path).size;
    const length = Math.min(size, this.#policy.loadTailBytes);
    const start = size - length;
    const descriptor = openSync(this.path, "r");
    const bytes = Buffer.alloc(length);
    try {
      readSync(descriptor, bytes, 0, length, start);
    } finally {
      closeSync(descriptor);
    }
    let text = bytes.toString("utf-8");
    let malformed = start > 0;
    if (start > 0) {
      const firstNewline = text.indexOf("\n");
      text = firstNewline === -1 ? "" : text.slice(firstNewline + 1);
    }
    for (const line of text.split("\n")) {
      if (!line.trim()) continue;
      if (Buffer.byteLength(line, "utf-8") > this.#policy.maxRecordBytes) {
        malformed = true;
        continue;
      }
      const value = parseJsonRecord(line);
      if (!value) {
        malformed = true;
        continue;
      }
      if (value.record_type === "command") {
        const command = parsePersistedCommand(value);
        if (!command) {
          malformed = true;
          continue;
        }
        this.#commands.set(commandKey(command.clientRunId, command.commandId), command);
        continue;
      }
      const frame = parsePersistedFrame(value);
      if (!frame) {
        malformed = true;
        continue;
      }
      try {
        this.#assertScopeAvailable(frame.clientRunId, frame.runId);
        this.#addLoadedFrame(frame);
      } catch {
        malformed = true;
      }
    }
    this.#downgradeMissingCommandResponses();
    return malformed || size > this.#policy.maxFileBytes;
  }

  #addLoadedFrame(frame: RetainedRunFrame): void {
    const frames = this.#framesByClientRunId.get(frame.clientRunId) ?? [];
    if (frames.some((existing) => existing.eventId === frame.eventId)) return;
    if (frames.some((existing) => existing.sequence === frame.sequence)) return;
    frames.push(frame);
    frames.sort((first, second) => first.sequence - second.sequence);
    this.#framesByClientRunId.set(frame.clientRunId, frames);
    if (frame.runId) {
      this.#runIdByClientRunId.set(frame.clientRunId, frame.runId);
      this.#clientRunIdByRunId.set(frame.runId, frame.clientRunId);
    }
  }

  #frameByEventId(eventId: string): RetainedRunFrame | null {
    for (const frames of this.#framesByClientRunId.values()) {
      const frame = frames.find((candidate) => candidate.eventId === eventId);
      if (frame) return frame;
    }
    return null;
  }

  #appendLine(line: string): void {
    if (Buffer.byteLength(line, "utf-8") > this.#policy.maxRecordBytes) {
      throw new Error("sanitized run transcript record exceeds its size limit");
    }
    mkdirSync(dirname(this.path), { recursive: true });
    appendFileSync(this.path, `${line}\n`, { encoding: "utf-8", mode: 0o600 });
  }

  #enforceRetention(force: boolean): void {
    const now = Date.now();
    const cutoff = now - this.#policy.maxAgeMs;
    let changed = false;

    for (const [clientRunId, original] of this.#framesByClientRunId) {
      const sorted = [...original].sort((first, second) => first.sequence - second.sequence);
      const newest = sorted.at(-1);
      const retainedByAge = sorted.filter(
        (frame) => Date.parse(frame.occurredAt) >= cutoff || frame === newest,
      );
      const retained = retainedByAge.slice(-this.#policy.maxFramesPerRun);
      if (retained.length !== original.length) changed = true;
      if (retained.length === 0) this.#framesByClientRunId.delete(clientRunId);
      else this.#framesByClientRunId.set(clientRunId, retained);
    }

    const allFrames = this.#allFrames();
    if (allFrames.length > this.#policy.maxFrames) {
      const retainedIds = new Set(
        allFrames
          .sort(compareFrameOccurrence)
          .slice(-this.#policy.maxFrames)
          .map((frame) => frame.eventId),
      );
      this.#filterFrames((frame) => retainedIds.has(frame.eventId));
      changed = true;
    }

    const commands = [...this.#commands.entries()].sort((first, second) =>
      compareOccurredAt(first[1].occurredAt, second[1].occurredAt),
    );
    const retainedCommands = commands
      .filter(([, command]) => Date.parse(command.occurredAt) >= cutoff)
      .slice(-this.#policy.maxCommands);
    if (retainedCommands.length !== commands.length) {
      this.#commands.clear();
      for (const [key, command] of retainedCommands) this.#commands.set(key, command);
      changed = true;
    }

    if (this.#downgradeMissingCommandResponses()) changed = true;
    this.#rebuildScopeMaps();

    let serialized = this.#serializedRecords();
    let totalBytes = serialized.reduce(
      (total, record) => total + Buffer.byteLength(record.line, "utf-8") + 1,
      0,
    );
    if (totalBytes > this.#policy.maxFileBytes) {
      const retained = new Set<string>();
      let retainedBytes = 0;
      for (const record of [...serialized].reverse()) {
        const bytes = Buffer.byteLength(record.line, "utf-8") + 1;
        if (retainedBytes + bytes > this.#policy.maxFileBytes) continue;
        retained.add(recordIdentity(record));
        retainedBytes += bytes;
      }
      this.#filterFrames((frame) => retained.has(`frame:${frame.eventId}`));
      for (const key of [...this.#commands.keys()]) {
        if (!retained.has(`command:${key}`)) this.#commands.delete(key);
      }
      this.#downgradeMissingCommandResponses();
      this.#rebuildScopeMaps();
      serialized = this.#serializedRecords();
      totalBytes = serialized.reduce(
        (total, record) => total + Buffer.byteLength(record.line, "utf-8") + 1,
        0,
      );
      changed = true;
    }

    const physicalTooLarge =
      existsSync(this.path) && statSync(this.path).size > this.#policy.maxFileBytes;
    if (force || changed || physicalTooLarge || totalBytes > this.#policy.maxFileBytes) {
      this.#compact(serialized);
    }
  }

  #serializedRecords(): SerializedRecord[] {
    const frames: SerializedRecord[] = this.#allFrames().map((frame) => ({
      eventId: frame.eventId,
      line: serializeFrame(frame),
      occurredAt: frame.occurredAt,
    }));
    const commands: SerializedRecord[] = [...this.#commands.entries()].map(([key, command]) => ({
      commandKey: key,
      line: serializeCommand(command),
      occurredAt: command.occurredAt,
    }));
    return [...frames, ...commands].sort((first, second) => {
      const occurrence = compareOccurredAt(first.occurredAt, second.occurredAt);
      if (occurrence !== 0) return occurrence;
      if (first.eventId && second.commandKey) return -1;
      if (first.commandKey && second.eventId) return 1;
      return recordIdentity(first).localeCompare(recordIdentity(second));
    });
  }

  #compact(records: SerializedRecord[]): void {
    mkdirSync(dirname(this.path), { recursive: true });
    const temporary = `${this.path}.${process.pid}.${randomUUID()}.tmp`;
    const descriptor = openSync(temporary, "wx", 0o600);
    try {
      const body =
        records.length === 0 ? "" : `${records.map((record) => record.line).join("\n")}\n`;
      writeFileSync(descriptor, body, "utf-8");
      fsyncSync(descriptor);
      closeSync(descriptor);
      renameSync(temporary, this.path);
      const directoryDescriptor = openSync(dirname(this.path), "r");
      try {
        fsyncSync(directoryDescriptor);
      } finally {
        closeSync(directoryDescriptor);
      }
    } catch (error) {
      try {
        closeSync(descriptor);
      } catch {
        // Descriptor was already closed after the successful fsync.
      }
      rmSync(temporary, { force: true });
      throw error;
    }
  }

  #allFrames(): RetainedRunFrame[] {
    return [...this.#framesByClientRunId.values()].flat();
  }

  #filterFrames(predicate: (frame: RetainedRunFrame) => boolean): void {
    for (const [clientRunId, frames] of this.#framesByClientRunId) {
      const retained = frames.filter(predicate);
      if (retained.length === 0) this.#framesByClientRunId.delete(clientRunId);
      else this.#framesByClientRunId.set(clientRunId, retained);
    }
  }

  #downgradeMissingCommandResponses(): boolean {
    let changed = false;
    for (const [key, command] of this.#commands) {
      if (
        command.status === "completed" &&
        (!command.responseEventId || !this.#frameByEventId(command.responseEventId))
      ) {
        const pending = { ...command, status: "pending" as const };
        delete pending.responseEventId;
        this.#commands.set(key, pending);
        changed = true;
      }
    }
    return changed;
  }

  #rebuildScopeMaps(): void {
    this.#clientRunIdByRunId.clear();
    this.#runIdByClientRunId.clear();
    for (const frame of this.#allFrames().sort(compareFrameOccurrence)) {
      if (!frame.runId) continue;
      const existingRunId = this.#runIdByClientRunId.get(frame.clientRunId);
      const existingClientRunId = this.#clientRunIdByRunId.get(frame.runId);
      if (existingRunId && existingRunId !== frame.runId) continue;
      if (existingClientRunId && existingClientRunId !== frame.clientRunId) continue;
      this.#runIdByClientRunId.set(frame.clientRunId, frame.runId);
      this.#clientRunIdByRunId.set(frame.runId, frame.clientRunId);
    }
  }

  #assertScopeAvailable(clientRunId: string, runId?: string | null): void {
    if (!runId) return;
    const existingRunId = this.#runIdByClientRunId.get(clientRunId);
    if (existingRunId && existingRunId !== runId) {
      throw new Error("client_run_id is already associated with a different engine run");
    }
    const existingClientRunId = this.#clientRunIdByRunId.get(runId);
    if (existingClientRunId && existingClientRunId !== clientRunId) {
      throw new Error("engine run is already associated with a different client_run_id");
    }
  }
}

function validatePolicy(policy: RunTranscriptRetentionPolicy): RunTranscriptRetentionPolicy {
  for (const value of Object.values(policy)) {
    if (!Number.isInteger(value) || value < 1) {
      throw new Error("run transcript retention limits must be positive integers");
    }
  }
  return policy;
}

function serializeFrame(frame: RetainedRunFrame): string {
  const persisted: PersistedRunFrame = {
    record_type: "frame",
    transcript_protocol_version: TRANSCRIPT_PROTOCOL_VERSION,
    client_run_id: frame.clientRunId,
    event_id: frame.eventId,
    occurred_at: frame.occurredAt,
    ...(frame.runId ? { run_id: frame.runId } : {}),
    sequence: frame.sequence,
    wire: frame.wire,
  };
  return JSON.stringify(persisted);
}

function serializeCommand(command: CommandRecord): string {
  const persisted: PersistedCommandRecord = {
    record_type: "command",
    transcript_protocol_version: TRANSCRIPT_PROTOCOL_VERSION,
    client_run_id: command.clientRunId,
    command_id: command.commandId,
    fingerprint: command.fingerprint,
    occurred_at: command.occurredAt,
    status: command.status,
    ...(command.responseEventId ? { response_event_id: command.responseEventId } : {}),
  };
  return JSON.stringify(persisted);
}

function parsePersistedFrame(value: Record<string, unknown>): RetainedRunFrame | null {
  try {
    if (value.record_type !== undefined && value.record_type !== "frame") return null;
    if (value.transcript_protocol_version !== TRANSCRIPT_PROTOCOL_VERSION) return null;
    if (typeof value.client_run_id !== "string" || value.client_run_id.length === 0) return null;
    if (typeof value.event_id !== "string" || value.event_id.length === 0) return null;
    if (typeof value.occurred_at !== "string" || !Number.isFinite(Date.parse(value.occurred_at))) {
      return null;
    }
    if (
      typeof value.sequence !== "number" ||
      !Number.isInteger(value.sequence) ||
      value.sequence < 1
    ) {
      return null;
    }
    if (typeof value.wire !== "string") return null;

    const decoded = parseJsonRecord(value.wire);
    if (!decoded) return null;
    const parsed = RetainedRunMessageSchema.safeParse(decoded);
    if (!parsed.success || !RETAINED_MESSAGE_TYPES.has(parsed.data.type)) return null;
    if (decoded.client_run_id !== value.client_run_id) return null;
    if (decoded.event_id !== value.event_id) return null;
    if (decoded.sequence !== value.sequence) return null;
    if (decoded.occurred_at !== value.occurred_at) return null;

    const runId =
      typeof value.run_id === "string" && value.run_id.length > 0
        ? value.run_id
        : readRunId(parsed.data);
    return {
      clientRunId: value.client_run_id,
      eventId: value.event_id,
      message: parsed.data,
      occurredAt: value.occurred_at,
      runId,
      sequence: value.sequence,
      wire: value.wire,
    };
  } catch {
    return null;
  }
}

function parsePersistedCommand(value: Record<string, unknown>): CommandRecord | null {
  if (value.transcript_protocol_version !== TRANSCRIPT_PROTOCOL_VERSION) return null;
  if (typeof value.client_run_id !== "string" || value.client_run_id.length === 0) return null;
  if (typeof value.command_id !== "string" || value.command_id.length === 0) return null;
  if (typeof value.fingerprint !== "string" || value.fingerprint.length === 0) return null;
  if (typeof value.occurred_at !== "string" || !Number.isFinite(Date.parse(value.occurred_at))) {
    return null;
  }
  if (value.status !== "pending" && value.status !== "completed") return null;
  const responseEventId =
    typeof value.response_event_id === "string" && value.response_event_id.length > 0
      ? value.response_event_id
      : undefined;
  return {
    clientRunId: value.client_run_id,
    commandId: value.command_id,
    fingerprint: value.fingerprint,
    occurredAt: value.occurred_at,
    status: value.status,
    ...(responseEventId ? { responseEventId } : {}),
  };
}

function fingerprintCommand(command: ClientMessage): string {
  const entries = Object.entries(command).filter(([key]) => key !== "command_id");
  const canonical = canonicalJson(Object.fromEntries(entries));
  return createHash("sha256").update(canonical).digest("hex");
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(",")}]`;
  const entries = Object.entries(value as Record<string, unknown>).sort(([first], [second]) =>
    first.localeCompare(second),
  );
  return `{${entries
    .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
    .join(",")}}`;
}

function commandKey(clientRunId: string, commandId: string): string {
  return JSON.stringify([clientRunId, commandId]);
}

function normalizeOccurredAt(value?: string): string {
  if (value && Number.isFinite(Date.parse(value))) return new Date(value).toISOString();
  return new Date().toISOString();
}

type CommandResponseMessage = Extract<
  ServerMessage,
  { type: "ack" | "chat_response" | "error" | "run_accepted" }
>;

function supportsCommandId(message: ServerMessage): message is CommandResponseMessage {
  return (
    message.type === "ack" ||
    message.type === "chat_response" ||
    message.type === "error" ||
    message.type === "run_accepted"
  );
}

function readCommandId(message: ServerMessage): string | null {
  if (supportsCommandId(message)) return message.command_id ?? null;
  if (message.type !== "event" || message.event !== "run_stopped") return null;
  const commandId = message.payload.command_id;
  return typeof commandId === "string" && commandId.length > 0 ? commandId : null;
}

function readRunId(message: ServerMessage): string | null {
  if ("run_id" in message && typeof message.run_id === "string" && message.run_id.length > 0) {
    return message.run_id;
  }
  if (message.type === "event") {
    const runId = message.payload.run_id;
    return typeof runId === "string" && runId.length > 0 ? runId : null;
  }
  if (message.type === "monitor_alert" && message.scope.startsWith("run:")) {
    return message.scope.slice("run:".length) || null;
  }
  return null;
}

interface RunStoppedPayload {
  bestScore?: number;
  commandId: string;
  completedGenerations: number;
  runId: string;
}

function readRunStoppedPayload(message: ServerMessage): RunStoppedPayload | null {
  if (message.type !== "event" || message.event !== "run_stopped") return null;
  const runId = message.payload.run_id;
  const reason = message.payload.reason;
  const commandId = message.payload.command_id;
  const completedGenerations = message.payload.completed_generations;
  const bestScore = message.payload.best_score;
  if (
    typeof runId !== "string" ||
    runId.length === 0 ||
    reason !== "operator" ||
    typeof commandId !== "string" ||
    commandId.length === 0 ||
    typeof completedGenerations !== "number" ||
    !Number.isInteger(completedGenerations) ||
    completedGenerations < 0 ||
    (bestScore !== undefined && (typeof bestScore !== "number" || !Number.isFinite(bestScore)))
  ) {
    return null;
  }
  return {
    runId,
    commandId,
    completedGenerations,
    ...(bestScore === undefined ? {} : { bestScore }),
  };
}

function parseJsonRecord(value: string): Record<string, unknown> | null {
  try {
    const parsed: unknown = JSON.parse(value);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return null;
    return Object.fromEntries(Object.entries(parsed));
  } catch {
    return null;
  }
}

function compareOccurredAt(first: string, second: string): number {
  return Date.parse(first) - Date.parse(second);
}

function compareFrameOccurrence(first: RetainedRunFrame, second: RetainedRunFrame): number {
  const occurredAt = compareOccurredAt(first.occurredAt, second.occurredAt);
  return occurredAt === 0 ? first.eventId.localeCompare(second.eventId) : occurredAt;
}

function recordIdentity(record: SerializedRecord): string {
  if (record.eventId) return `frame:${record.eventId}`;
  return `command:${record.commandKey ?? ""}`;
}
