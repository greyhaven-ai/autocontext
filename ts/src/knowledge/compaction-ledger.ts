import {
  closeSync,
  existsSync,
  mkdirSync,
  openSync,
  readFileSync,
  readSync,
  statSync,
  writeFileSync,
  appendFileSync,
} from "node:fs";
import { dirname, isAbsolute, relative, resolve } from "node:path";

const COMPACTION_LEDGER_TAIL_BYTES = 64 * 1024;

export interface CompactionEntry {
  type?: "compaction";
  id: string;
  parentId: string;
  timestamp: string;
  summary: string;
  firstKeptEntryId: string;
  tokensBefore: number;
  details?: Record<string, unknown>;
}

export class CompactionLedgerStore {
  readonly runsRoot: string;

  constructor(runsRoot: string) {
    this.runsRoot = runsRoot;
  }

  ledgerPath(runId: string): string {
    return this.resolveRunPath(runId, "compactions.jsonl");
  }

  latestEntryPath(runId: string): string {
    return this.resolveRunPath(runId, "compactions.latest");
  }

  appendEntries(runId: string, entries: CompactionEntry[]): void {
    if (entries.length === 0) return;
    const path = this.ledgerPath(runId);
    mkdirSync(dirname(path), { recursive: true });
    appendFileSync(path, serializeCompactionEntries(entries), "utf-8");
    writeFileSync(this.latestEntryPath(runId), `${entries.at(-1)!.id}\n`, "utf-8");
  }

  readEntries(runId: string, opts: { limit?: number } = {}): CompactionEntry[] {
    const limit = opts.limit ?? 20;
    const path = this.ledgerPath(runId);
    if (!existsSync(path)) return [];
    let text: string;
    let truncated: boolean;
    if (limit <= 0) {
      text = readFileSync(path, "utf-8");
      truncated = false;
    } else {
      [text, truncated] = readTailText(path, COMPACTION_LEDGER_TAIL_BYTES);
    }
    const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const parseableLines = truncated ? lines.slice(1) : lines;
    const entries = parseableLines
      .map(parseEntry)
      .filter((entry): entry is CompactionEntry => entry !== null);
    return limit > 0 ? entries.slice(-limit) : entries;
  }

  latestEntryId(runId: string): string {
    const latestPath = this.latestEntryPath(runId);
    if (existsSync(latestPath)) {
      return readFileSync(latestPath, "utf-8").trim();
    }
    const path = this.ledgerPath(runId);
    if (!existsSync(path)) return "";
    const [text, truncated] = readTailText(path, COMPACTION_LEDGER_TAIL_BYTES);
    const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    const parseableLines = truncated ? lines.slice(1) : lines;
    for (const line of parseableLines.reverse()) {
      const entry = parseEntry(line);
      if (entry) return entry.id;
    }
    return "";
  }

  private resolveRunPath(runId: string, fileName: string): string {
    const root = resolve(this.runsRoot);
    const runDir = resolve(root, runId);
    const relativeRunPath = relative(root, runDir);
    if (!relativeRunPath || relativeRunPath.startsWith("..") || isAbsolute(relativeRunPath)) {
      throw new Error("run_id must stay within the runs root");
    }
    return resolve(runDir, fileName);
  }
}

export function serializeCompactionEntries(entries: CompactionEntry[]): string {
  return entries.map((entry) => JSON.stringify(normalizeCompactionEntry(entry))).join("\n") + "\n";
}

export function normalizeCompactionEntry(entry: CompactionEntry): Required<CompactionEntry> {
  return {
    type: "compaction",
    id: entry.id,
    parentId: entry.parentId,
    timestamp: entry.timestamp,
    summary: entry.summary,
    firstKeptEntryId: entry.firstKeptEntryId,
    tokensBefore: entry.tokensBefore,
    details: entry.details ?? {},
  };
}

function parseEntry(line: string): CompactionEntry | null {
  try {
    const parsed: unknown = JSON.parse(line);
    if (!isRecord(parsed) || parsed.type !== "compaction" || typeof parsed.id !== "string") {
      return null;
    }
    return {
      type: "compaction",
      id: parsed.id,
      parentId: readString(parsed, "parentId"),
      timestamp: readString(parsed, "timestamp"),
      summary: readString(parsed, "summary"),
      firstKeptEntryId: readString(parsed, "firstKeptEntryId"),
      tokensBefore: readNumber(parsed, "tokensBefore"),
      details: isRecord(parsed.details) ? parsed.details : {},
    };
  } catch {
    return null;
  }
}

function readTailText(path: string, maxBytes: number): readonly [string, boolean] {
  const { size } = statSync(path);
  if (size <= 0) return ["", false];
  const bytesToRead = Math.min(size, maxBytes);
  const start = size - bytesToRead;
  const buffer = Buffer.allocUnsafe(bytesToRead);
  const fd = openSync(path, "r");
  try {
    let offset = 0;
    while (offset < bytesToRead) {
      const bytesRead = readSync(fd, buffer, offset, bytesToRead - offset, start + offset);
      if (bytesRead === 0) break;
      offset += bytesRead;
    }
    return [buffer.subarray(0, offset).toString("utf-8"), start > 0];
  } finally {
    closeSync(fd);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readString(value: Record<string, unknown>, key: string): string {
  const raw = value[key];
  return typeof raw === "string" ? raw : "";
}

function readNumber(value: Record<string, unknown>, key: string): number {
  const raw = value[key];
  return typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
}
