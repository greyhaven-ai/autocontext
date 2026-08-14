import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { randomUUID } from "node:crypto";
import { dirname, join } from "node:path";

import { canonicalJsonStringify } from "../contract/canonical-json.js";
import type {
  RuntimeActivationFailureCode,
  RuntimeActivationJournalOutcome,
  RuntimeActivationJournalRecord,
  RuntimeActivationJournalStore,
  RuntimeActivationOperation,
  RuntimeActivationStage,
  RuntimeActivationTargetMode,
} from "./types.js";

const JOURNAL_DIR = join(".autocontext", "state", "runtime-activation-journal");

export class InMemoryRuntimeActivationJournalStore implements RuntimeActivationJournalStore {
  private readonly records = new Map<string, RuntimeActivationJournalRecord>();

  load(transactionId: string): RuntimeActivationJournalRecord | null {
    const record = this.records.get(transactionId);
    return record ? cloneRecord(record) : null;
  }

  save(record: RuntimeActivationJournalRecord): void {
    validateRuntimeActivationJournalRecord(record);
    this.records.set(record.transactionId, cloneRecord(record));
  }

  list(): RuntimeActivationJournalRecord[] {
    return [...this.records.values()]
      .map(cloneRecord)
      .sort((left, right) => left.createdAt.localeCompare(right.createdAt)
        || left.transactionId.localeCompare(right.transactionId));
  }
}

export class FileRuntimeActivationJournalStore implements RuntimeActivationJournalStore {
  private readonly root: string;

  constructor(registryRoot: string) {
    this.root = join(registryRoot, JOURNAL_DIR);
  }

  load(transactionId: string): RuntimeActivationJournalRecord | null {
    const path = this.pathFor(transactionId);
    if (!existsSync(path)) return null;
    return readJournalRecord(path);
  }

  save(record: RuntimeActivationJournalRecord): void {
    validateRuntimeActivationJournalRecord(record);
    const path = this.pathFor(record.transactionId);
    mkdirSync(dirname(path), { recursive: true });
    const tmp = `${path}.${process.pid}.${randomUUID()}.tmp`;
    try {
      writeFileSync(tmp, canonicalJsonStringify(record), "utf-8");
      renameSync(tmp, path);
    } finally {
      if (existsSync(tmp)) unlinkSync(tmp);
    }
  }

  list(): RuntimeActivationJournalRecord[] {
    if (!existsSync(this.root)) return [];
    return readdirSync(this.root)
      .filter((name) => name.endsWith(".json"))
      .map((name) => readJournalRecord(join(this.root, name)))
      .sort((left, right) => left.createdAt.localeCompare(right.createdAt)
        || left.transactionId.localeCompare(right.transactionId));
  }

  private pathFor(transactionId: string): string {
    return join(this.root, `${encodeURIComponent(validateTransactionId(transactionId))}.json`);
  }
}

export function validateRuntimeActivationJournalRecord(
  record: unknown,
): asserts record is RuntimeActivationJournalRecord {
  if (!isUnknownRecord(record)) {
    throw new Error("runtime activation journal must be an object");
  }
  if (typeof record.transactionId !== "string") {
    throw new Error("runtime activation journal transaction id required");
  }
  validateTransactionId(record.transactionId);
  if (record.schemaVersion !== 1) throw new Error("unsupported runtime activation journal schema");
  if (!isRuntimeActivationOperation(record.operation)) {
    throw new Error("runtime activation journal operation is invalid");
  }
  if (!isNullableString(record.candidateArtifactId) || !isNullableString(record.priorArtifactId)) {
    throw new Error("runtime activation journal artifact id is invalid");
  }
  if (!isRuntimeActivationTargetMode(record.targetMode)) {
    throw new Error("runtime activation journal target mode is invalid");
  }
  if (
    record.requestKey !== undefined
    && (
      typeof record.requestKey !== "string"
      || record.requestKey.length === 0
      || record.requestKey.length > 1_000
      || /[\u0000-\u001f\u007f]/.test(record.requestKey)
    )
  ) {
    throw new Error("runtime activation journal request key is invalid");
  }
  if (!isRuntimeActivationStage(record.stage)) {
    throw new Error("runtime activation journal stage is invalid");
  }
  if (!isRuntimeActivationJournalOutcome(record.outcome)) {
    throw new Error("runtime activation journal outcome is invalid");
  }
  if (
    record.failureCode !== undefined
    && !isRuntimeActivationFailureCode(record.failureCode)
  ) {
    throw new Error("runtime activation journal failure code is invalid");
  }
  if (typeof record.createdAt !== "string" || typeof record.updatedAt !== "string") {
    throw new Error("runtime activation journal timestamps required");
  }
  if (!Array.isArray(record.entries)) throw new Error("runtime activation journal entries required");
  for (let index = 0; index < record.entries.length; index += 1) {
    const entry = record.entries[index];
    if (
      !isUnknownRecord(entry)
      || entry.sequence !== index
      || !isRuntimeActivationStage(entry.stage)
      || (entry.outcome !== "started" && entry.outcome !== "succeeded" && entry.outcome !== "failed")
      || typeof entry.timestamp !== "string"
      || (
        entry.failureCode !== undefined
        && !isRuntimeActivationFailureCode(entry.failureCode)
      )
    ) {
      throw new Error("runtime activation journal entry is invalid");
    }
  }
}

function readJournalRecord(path: string): RuntimeActivationJournalRecord {
  let parsed: unknown;
  try {
    parsed = JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    throw new Error(`runtime activation journal is unreadable: ${path}`);
  }
  try {
    validateRuntimeActivationJournalRecord(parsed);
  } catch {
    throw new Error(`runtime activation journal is invalid: ${path}`);
  }
  return cloneRecord(parsed);
}

function isUnknownRecord(value: unknown): value is Record<PropertyKey, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableString(value: unknown): value is string | null {
  return typeof value === "string" || value === null;
}

function isRuntimeActivationOperation(value: unknown): value is RuntimeActivationOperation {
  return value === "activate" || value === "rollback" || value === "repair";
}

function isRuntimeActivationTargetMode(value: unknown): value is RuntimeActivationTargetMode {
  return value === "candidate" || value === "shadow" || value === "canary" || value === "active";
}

function isRuntimeActivationStage(value: unknown): value is RuntimeActivationStage {
  return value === "staged"
    || value === "applying"
    || value === "applied"
    || value === "validating"
    || value === "validated"
    || value === "activating"
    || value === "activated"
    || value === "draining"
    || value === "drained"
    || value === "cutting_over"
    || value === "runtime_cutover"
    || value === "pointer_cutover"
    || value === "disposing_prior"
    || value === "reverting"
    || value === "restored"
    || value === "committed"
    || value === "failed";
}

function isRuntimeActivationJournalOutcome(
  value: unknown,
): value is RuntimeActivationJournalOutcome {
  return value === "in_progress"
    || value === "succeeded"
    || value === "failed"
    || value === "recovered"
    || value === "diverged";
}

function isRuntimeActivationFailureCode(value: unknown): value is RuntimeActivationFailureCode {
  return value === "apply_failed"
    || value === "validation_failed"
    || value === "activation_failed"
    || value === "drain_failed"
    || value === "cutover_failed"
    || value === "pointer_failed"
    || value === "disposal_failed"
    || value === "rollback_failed"
    || value === "restore_failed"
    || value === "effect_policy_denied"
    || value === "metadata_failed"
    || value === "observed_state_mismatch";
}

function validateTransactionId(transactionId: string): string {
  const normalized = transactionId.trim();
  if (
    !normalized
    || normalized.length > 160
    || /[\u0000-\u001f\u007f/\\]/.test(normalized)
  ) {
    throw new Error("runtime activation transaction id is invalid");
  }
  return normalized;
}

function cloneRecord(record: RuntimeActivationJournalRecord): RuntimeActivationJournalRecord {
  return {
    ...record,
    entries: record.entries.map((entry) => ({ ...entry })),
  };
}
