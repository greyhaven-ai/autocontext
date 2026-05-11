import {
  ActorRef,
  RunTrace,
  TraceEvent,
} from "./run-trace.js";
import {
  RuntimeSessionEventType,
  type RuntimeSessionEvent,
  type RuntimeSessionEventLog,
} from "../session/runtime-events.js";
import { jsonSafeRecord } from "../session/runtime-json.js";

export interface RuntimeSessionRunTraceOpts {
  runId?: string;
  scenarioType?: string;
  childLogs?: readonly RuntimeSessionEventLog[];
  createdAt?: string;
}

interface RuntimeEventRecord {
  event: RuntimeSessionEvent;
  log: RuntimeSessionEventLog;
  logIndex: number;
}

export function runtimeSessionLogToRunTrace(
  log: RuntimeSessionEventLog,
  opts: RuntimeSessionRunTraceOpts = {},
): RunTrace {
  const records = flattenRuntimeEvents(log, opts.childLogs ?? []);
  const trace = new RunTrace(
    opts.runId ?? inferRunId(log),
    opts.scenarioType ?? inferScenarioType(log),
    opts.createdAt ?? records[0]?.event.timestamp ?? log.createdAt,
  );

  for (const record of records) {
    trace.addEvent(new TraceEvent({
      eventType: traceEventType(record.event),
      actor: actorFor(record),
      payload: detailFor(record),
      timestamp: record.event.timestamp,
    }));
  }
  return trace;
}

function flattenRuntimeEvents(
  log: RuntimeSessionEventLog,
  childLogs: readonly RuntimeSessionEventLog[],
): RuntimeEventRecord[] {
  const logs = [log, ...childLogs];
  return logs
    .flatMap((currentLog, logIndex) =>
      currentLog.events.map((event) => ({ event, log: currentLog, logIndex })))
    .sort(compareRuntimeEventRecords);
}

function compareRuntimeEventRecords(a: RuntimeEventRecord, b: RuntimeEventRecord): number {
  return compareString(a.event.timestamp, b.event.timestamp)
    || a.logIndex - b.logIndex
    || a.event.sequence - b.event.sequence
    || compareString(a.event.eventId, b.event.eventId);
}

function traceEventType(event: RuntimeSessionEvent): string {
  return `runtime_${event.eventType}`;
}

function actorFor(record: RuntimeEventRecord): ActorRef {
  const { event, log } = record;
  const payload = event.payload;
  if (event.eventType === RuntimeSessionEventType.SHELL_COMMAND) {
    const commandName = readString(payload.commandName) || readString(payload.command) || "command";
    return new ActorRef("tool", commandName, commandName);
  }
  if (event.eventType === RuntimeSessionEventType.TOOL_CALL) {
    const toolName = readString(payload.toolName) || readString(payload.tool) || "tool";
    return new ActorRef("tool", toolName, toolName);
  }
  if (
    event.eventType === RuntimeSessionEventType.CHILD_TASK_STARTED
    || event.eventType === RuntimeSessionEventType.CHILD_TASK_COMPLETED
  ) {
    return new ActorRef("system", "runtime_session", "runtime_session");
  }
  if (event.eventType === RuntimeSessionEventType.COMPACTION) {
    return new ActorRef("system", "compaction_ledger", "compaction_ledger");
  }
  const role = readString(payload.role) || readString(log.metadata.role) || "runtime";
  return new ActorRef("role", role, role);
}

function detailFor(record: RuntimeEventRecord): Record<string, unknown> {
  const { event, log } = record;
  const payload = event.payload;
  const detail: Record<string, unknown> = {
    runtime_session_id: event.sessionId || log.sessionId,
    runtime_event_id: event.eventId,
    runtime_event_type: event.eventType,
    sequence: event.sequence,
    parent_session_id: event.parentSessionId || log.parentSessionId,
    task_id: event.taskId || log.taskId,
    worker_id: event.workerId || log.workerId,
  };

  copyString(payload, detail, "requestId", "request_id");
  copyString(payload, detail, "promptEventId", "prompt_event_id");
  copyString(payload, detail, "role", "role");
  copyString(payload, detail, "cwd", "cwd");
  copyString(payload, detail, "phase", "phase");
  copyString(payload, detail, "commandName", "command_name");
  copyString(payload, detail, "command", "command_name");
  copyString(payload, detail, "toolName", "tool_name");
  copyString(payload, detail, "tool", "tool_name");
  copyString(payload, detail, "argsSummary", "args_summary");
  copyString(payload, detail, "taskId", "task_id");
  copyString(payload, detail, "childSessionId", "child_session_id");
  copyString(payload, detail, "workerId", "worker_id");
  copyString(payload, detail, "entryId", "entry_id");
  copyString(payload, detail, "components", "components");
  copyString(payload, detail, "ledgerPath", "ledger_path");
  copyString(payload, detail, "latestEntryPath", "latest_entry_path");
  copyString(payload, detail, "firstKeptEntryId", "first_kept_entry_id");
  copyString(payload, detail, "promotedKnowledgeId", "promoted_knowledge_id");
  copyString(payload, detail, "runId", "run_id");
  copyNumber(payload, detail, "exitCode", "exit_code");
  copyNumber(payload, detail, "depth", "depth");
  copyNumber(payload, detail, "maxDepth", "max_depth");
  copyNumber(payload, detail, "entryCount", "entry_count");
  copyNumber(payload, detail, "generation", "generation");
  copyNumber(payload, detail, "tokensBefore", "tokens_before");
  copyBoolean(payload, detail, "isError", "is_error");
  copyStringArray(payload, detail, "entryIds", "entry_ids");

  return jsonSafeRecord(detail);
}

function inferRunId(log: RuntimeSessionEventLog): string {
  const metadataRunId = readString(log.metadata.runId);
  if (metadataRunId) return metadataRunId;
  for (const event of log.events) {
    const eventRunId = readString(event.payload.runId);
    if (eventRunId) return eventRunId;
  }
  const match = /^run:(.+):runtime$/.exec(log.sessionId);
  return match?.[1] ?? log.sessionId;
}

function inferScenarioType(log: RuntimeSessionEventLog): string {
  return readString(log.metadata.scenarioName)
    || readString(log.metadata.scenario)
    || "runtime_session";
}

function copyString(
  source: Record<string, unknown>,
  target: Record<string, unknown>,
  sourceKey: string,
  targetKey: string,
): void {
  const value = readString(source[sourceKey]);
  if (value && !readString(target[targetKey])) {
    target[targetKey] = value;
  }
}

function copyNumber(
  source: Record<string, unknown>,
  target: Record<string, unknown>,
  sourceKey: string,
  targetKey: string,
): void {
  const value = source[sourceKey];
  if (typeof value === "number" && Number.isFinite(value)) {
    target[targetKey] = value;
  }
}

function copyBoolean(
  source: Record<string, unknown>,
  target: Record<string, unknown>,
  sourceKey: string,
  targetKey: string,
): void {
  const value = source[sourceKey];
  if (typeof value === "boolean") {
    target[targetKey] = value;
  }
}

function copyStringArray(
  source: Record<string, unknown>,
  target: Record<string, unknown>,
  sourceKey: string,
  targetKey: string,
): void {
  const value = source[sourceKey];
  if (Array.isArray(value) && value.every((item) => typeof item === "string")) {
    target[targetKey] = [...value];
  }
}

function readString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function compareString(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}
