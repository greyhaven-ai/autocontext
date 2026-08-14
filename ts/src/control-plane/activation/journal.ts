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
  RuntimeActivationJournalRecord,
  RuntimeActivationJournalStore,
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
  record: RuntimeActivationJournalRecord,
): void {
  validateTransactionId(record.transactionId);
  if (record.schemaVersion !== 1) throw new Error("unsupported runtime activation journal schema");
  if (!Array.isArray(record.entries)) throw new Error("runtime activation journal entries required");
  for (let index = 0; index < record.entries.length; index += 1) {
    if (record.entries[index]?.sequence !== index) {
      throw new Error("runtime activation journal sequence is invalid");
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
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`runtime activation journal is invalid: ${path}`);
  }
  const record = parsed as RuntimeActivationJournalRecord;
  validateRuntimeActivationJournalRecord(record);
  return cloneRecord(record);
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
