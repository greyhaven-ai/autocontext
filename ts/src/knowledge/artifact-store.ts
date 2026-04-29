/**
 * Artifact store — file-based persistence for runs, knowledge, tools (AC-344 Task 10b).
 * Mirrors the core subset of Python's autocontext/storage/artifacts.py.
 */

import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, join, relative, resolve } from "node:path";
import { HookEvents, type HookBus } from "../extensions/index.js";
import { PlaybookManager, EMPTY_PLAYBOOK_SENTINEL } from "./playbook.js";
import {
  CompactionLedgerStore,
  normalizeCompactionEntry,
  serializeCompactionEntries,
} from "./compaction-ledger.js";
import type { CompactionEntry } from "./compaction-ledger.js";

export interface ArtifactStoreOpts {
  runsRoot: string;
  knowledgeRoot: string;
  maxPlaybookVersions?: number;
  hookBus?: HookBus | null;
}

interface ArtifactWriteRequest {
  path: string;
  format: "json" | "jsonl" | "markdown" | "text";
  append: boolean;
  payload?: Record<string, unknown>;
  content?: string;
  heading?: string;
}

export class ArtifactStore {
  readonly runsRoot: string;
  readonly knowledgeRoot: string;
  private playbookManager: PlaybookManager;
  private compactionLedger: CompactionLedgerStore;
  private hookBus: HookBus | null;

  constructor(opts: ArtifactStoreOpts) {
    this.runsRoot = opts.runsRoot;
    this.knowledgeRoot = opts.knowledgeRoot;
    this.hookBus = opts.hookBus ?? null;
    this.playbookManager = new PlaybookManager(
      opts.knowledgeRoot,
      opts.maxPlaybookVersions ?? 5,
    );
    this.compactionLedger = new CompactionLedgerStore(this.runsRoot);
  }

  generationDir(runId: string, generationIndex: number): string {
    return join(this.runsRoot, runId, "generations", `gen_${generationIndex}`);
  }

  compactionLedgerPath(runId: string): string {
    return this.compactionLedger.ledgerPath(runId);
  }

  compactionLatestEntryPath(runId: string): string {
    return this.compactionLedger.latestEntryPath(runId);
  }

  appendCompactionEntries(runId: string, entries: CompactionEntry[]): void {
    if (entries.length === 0) return;
    const normalizedEntries = entries.map(normalizeCompactionEntry);
    const ledgerRequest = this.applyArtifactWriteHook({
      path: this.compactionLedger.ledgerPath(runId),
      format: "jsonl",
      append: true,
      payload: { entries: normalizedEntries },
      content: serializeCompactionEntries(normalizedEntries),
    });
    const payloadEntries = readCompactionEntries(ledgerRequest.payload?.entries);
    const finalEntries = payloadEntries ?? normalizedEntries;
    const ledgerContent = payloadEntries
      ? serializeCompactionEntries(finalEntries)
      : ledgerRequest.content ?? serializeCompactionEntries(finalEntries);
    mkdirSync(dirname(ledgerRequest.path), { recursive: true });
    appendFileSync(ledgerRequest.path, ensureTrailingNewline(ledgerContent), "utf-8");

    const latestEntryId = finalEntries.at(-1)?.id ?? entries.at(-1)!.id;
    const latestRequest = this.applyArtifactWriteHook({
      path: this.compactionLedger.latestEntryPath(runId),
      format: "text",
      append: false,
      content: `${latestEntryId}\n`,
    });
    mkdirSync(dirname(latestRequest.path), { recursive: true });
    writeFileSync(
      latestRequest.path,
      ensureTrailingNewline(latestRequest.content ?? `${latestEntryId}\n`),
      "utf-8",
    );
  }

  readCompactionEntries(runId: string, opts: { limit?: number } = {}): CompactionEntry[] {
    return this.compactionLedger.readEntries(runId, opts);
  }

  latestCompactionEntryId(runId: string): string {
    return this.compactionLedger.latestEntryId(runId);
  }

  writeJson(path: string, payload: Record<string, unknown>): void {
    const request = this.applyArtifactWriteHook({
      path,
      format: "json",
      append: false,
      payload,
    });
    const finalPayload = request.payload ?? payload;
    mkdirSync(dirname(request.path), { recursive: true });
    writeFileSync(request.path, JSON.stringify(finalPayload, null, 2) + "\n", "utf-8");
  }

  writeMarkdown(path: string, content: string): void {
    const request = this.applyArtifactWriteHook({
      path,
      format: "markdown",
      append: false,
      content,
    });
    mkdirSync(dirname(request.path), { recursive: true });
    writeFileSync(request.path, (request.content ?? content).trim() + "\n", "utf-8");
  }

  appendMarkdown(path: string, content: string, heading: string): void {
    const request = this.applyArtifactWriteHook({
      path,
      format: "markdown",
      append: true,
      content,
      heading,
    });
    mkdirSync(dirname(request.path), { recursive: true });
    const chunk = `\n## ${request.heading ?? heading}\n\n${(request.content ?? content).trim()}\n`;
    if (existsSync(request.path)) {
      appendFileSync(request.path, chunk, "utf-8");
    } else {
      writeFileSync(request.path, chunk.replace(/^\n/, ""), "utf-8");
    }
  }

  readPlaybook(scenarioName: string): string {
    return this.playbookManager.read(scenarioName);
  }

  writePlaybook(scenarioName: string, content: string): void {
    const path = join(this.knowledgeRoot, scenarioName, "playbook.md");
    const request = this.applyArtifactWriteHook({
      path,
      format: "markdown",
      append: false,
      content,
    });
    const finalContent = request.content ?? content;
    if (resolve(request.path) === resolve(path)) {
      this.playbookManager.write(scenarioName, finalContent);
      return;
    }
    mkdirSync(dirname(request.path), { recursive: true });
    writeFileSync(request.path, finalContent.trim() + "\n", "utf-8");
  }

  readDeadEnds(scenarioName: string): string {
    const path = join(this.knowledgeRoot, scenarioName, "dead_ends.md");
    return existsSync(path) ? readFileSync(path, "utf-8") : "";
  }

  appendDeadEnd(scenarioName: string, entry: string): void {
    const path = join(this.knowledgeRoot, scenarioName, "dead_ends.md");
    const request = this.applyArtifactWriteHook({
      path,
      format: "markdown",
      append: true,
      content: entry,
      heading: "Dead End",
    });
    mkdirSync(dirname(request.path), { recursive: true });
    const chunk = `\n### ${request.heading ?? "Dead End"}\n\n${(request.content ?? entry).trim()}\n`;
    if (existsSync(request.path)) {
      appendFileSync(request.path, chunk, "utf-8");
    } else {
      writeFileSync(request.path, chunk.replace(/^\n/, ""), "utf-8");
    }
  }

  replaceDeadEnds(scenarioName: string, content: string): void {
    const path = join(this.knowledgeRoot, scenarioName, "dead_ends.md");
    this.writeMarkdown(path, content);
  }

  writeSessionReport(scenarioName: string, runId: string, content: string): string {
    const path = join(this.knowledgeRoot, scenarioName, "session_reports", `${runId}.md`);
    this.writeMarkdown(path, content);
    return path;
  }

  readNotebook(sessionId: string): Record<string, unknown> | null {
    const path = this.notebookPath(sessionId);
    if (!existsSync(path)) {
      return null;
    }
    const parsed = JSON.parse(readFileSync(path, "utf-8")) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : null;
  }

  writeNotebook(sessionId: string, notebook: Record<string, unknown>): void {
    this.writeJson(this.notebookPath(sessionId), notebook);
  }

  deleteNotebook(sessionId: string): void {
    const path = this.notebookPath(sessionId);
    if (existsSync(path)) {
      unlinkSync(path);
    }
  }

  private notebookPath(sessionId: string): string {
    const sessionsRoot = resolve(this.runsRoot, "sessions");
    const path = resolve(sessionsRoot, sessionId, "notebook.json");
    const relativePath = relative(sessionsRoot, path);
    if (relativePath.startsWith("..") || isAbsolute(relativePath)) {
      throw new Error("session_id must stay within the notebook sessions root");
    }
    return path;
  }

  private applyArtifactWriteHook(request: ArtifactWriteRequest): ArtifactWriteRequest {
    if (!this.hookBus?.hasHandlers(HookEvents.ARTIFACT_WRITE)) {
      return request;
    }
    const event = this.hookBus.emit(HookEvents.ARTIFACT_WRITE, {
      path: request.path,
      format: request.format,
      append: request.append,
      payload: request.payload,
      content: request.content,
      heading: request.heading,
    });
    event.raiseIfBlocked();

    const nextPath = readString(event.payload.path) ?? request.path;
    this.validateArtifactHookPath(request.path, nextPath);
    const result: ArtifactWriteRequest = {
      path: nextPath,
      format: request.format,
      append: request.append,
    };
    const nextPayload = event.payload.payload;
    if (isRecord(nextPayload)) {
      result.payload = nextPayload;
    } else if (request.payload !== undefined) {
      result.payload = request.payload;
    }
    const nextContent = readString(event.payload.content);
    if (nextContent !== null) {
      result.content = nextContent;
    } else if (request.content !== undefined) {
      result.content = request.content;
    }
    const nextHeading = readString(event.payload.heading);
    if (nextHeading !== null) {
      result.heading = nextHeading;
    } else if (request.heading !== undefined) {
      result.heading = request.heading;
    }
    return result;
  }

  private validateArtifactHookPath(originalPath: string, nextPath: string): void {
    if (resolve(originalPath) === resolve(nextPath)) {
      return;
    }
    const originalRoot = this.managedRootForPath(originalPath);
    if (!originalRoot || !pathIsInsideRoot(originalRoot, nextPath)) {
      throw new Error("artifact_write path must stay within the original managed root");
    }
  }

  private managedRootForPath(path: string): string | null {
    for (const root of [this.runsRoot, this.knowledgeRoot]) {
      if (pathIsInsideRoot(root, path)) {
        return resolve(root);
      }
    }
    return null;
  }

  readSessionReports(scenarioName: string, limit = 3): string {
    const dir = join(this.knowledgeRoot, scenarioName, "session_reports");
    if (!existsSync(dir)) return "";
    const reports = readdirSync(dir)
      .filter((name) => name.endsWith(".md"))
      .map((name) => {
        const path = join(dir, name);
        return {
          name,
          path,
          mtimeMs: statSync(path).mtimeMs,
        };
      })
      .sort((a, b) => b.mtimeMs - a.mtimeMs)
      .slice(0, limit)
      .map((entry) => `### ${entry.name.replace(/\.md$/, "")}\n\n${readFileSync(entry.path, "utf-8").trim()}`);

    return reports.join("\n\n").trim();
  }
}

export { EMPTY_PLAYBOOK_SENTINEL };

function readString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function readCompactionEntries(value: unknown): CompactionEntry[] | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const entries: CompactionEntry[] = [];
  for (const raw of value) {
    if (!isRecord(raw) || typeof raw.id !== "string") {
      return null;
    }
    entries.push({
      type: raw.type === "compaction" ? "compaction" : undefined,
      id: raw.id,
      parentId: typeof raw.parentId === "string" ? raw.parentId : "",
      timestamp: typeof raw.timestamp === "string" ? raw.timestamp : "",
      summary: typeof raw.summary === "string" ? raw.summary : "",
      firstKeptEntryId: typeof raw.firstKeptEntryId === "string" ? raw.firstKeptEntryId : "",
      tokensBefore: typeof raw.tokensBefore === "number" && Number.isFinite(raw.tokensBefore)
        ? raw.tokensBefore
        : 0,
      details: isRecord(raw.details) ? raw.details : {},
    });
  }
  return entries;
}

function ensureTrailingNewline(content: string): string {
  return content.endsWith("\n") ? content : `${content}\n`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function pathIsInsideRoot(root: string, path: string): boolean {
  const relativePath = relative(resolve(root), resolve(path));
  return relativePath === "" || (!relativePath.startsWith("..") && !isAbsolute(relativePath));
}
