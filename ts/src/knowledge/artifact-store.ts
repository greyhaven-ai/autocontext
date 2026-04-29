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
import { PlaybookManager, EMPTY_PLAYBOOK_SENTINEL } from "./playbook.js";
import { CompactionLedgerStore } from "./compaction-ledger.js";
import type { CompactionEntry } from "./compaction-ledger.js";

export interface ArtifactStoreOpts {
  runsRoot: string;
  knowledgeRoot: string;
  maxPlaybookVersions?: number;
}

export class ArtifactStore {
  readonly runsRoot: string;
  readonly knowledgeRoot: string;
  private playbookManager: PlaybookManager;
  private compactionLedger: CompactionLedgerStore;

  constructor(opts: ArtifactStoreOpts) {
    this.runsRoot = opts.runsRoot;
    this.knowledgeRoot = opts.knowledgeRoot;
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
    this.compactionLedger.appendEntries(runId, entries);
  }

  readCompactionEntries(runId: string, opts: { limit?: number } = {}): CompactionEntry[] {
    return this.compactionLedger.readEntries(runId, opts);
  }

  latestCompactionEntryId(runId: string): string {
    return this.compactionLedger.latestEntryId(runId);
  }

  writeJson(path: string, payload: Record<string, unknown>): void {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, JSON.stringify(payload, null, 2) + "\n", "utf-8");
  }

  writeMarkdown(path: string, content: string): void {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, content.trim() + "\n", "utf-8");
  }

  appendMarkdown(path: string, content: string, heading: string): void {
    mkdirSync(dirname(path), { recursive: true });
    const chunk = `\n## ${heading}\n\n${content.trim()}\n`;
    if (existsSync(path)) {
      appendFileSync(path, chunk, "utf-8");
    } else {
      writeFileSync(path, chunk.replace(/^\n/, ""), "utf-8");
    }
  }

  readPlaybook(scenarioName: string): string {
    return this.playbookManager.read(scenarioName);
  }

  writePlaybook(scenarioName: string, content: string): void {
    this.playbookManager.write(scenarioName, content);
  }

  readDeadEnds(scenarioName: string): string {
    const path = join(this.knowledgeRoot, scenarioName, "dead_ends.md");
    return existsSync(path) ? readFileSync(path, "utf-8") : "";
  }

  appendDeadEnd(scenarioName: string, entry: string): void {
    const path = join(this.knowledgeRoot, scenarioName, "dead_ends.md");
    mkdirSync(dirname(path), { recursive: true });
    const chunk = `\n### Dead End\n\n${entry.trim()}\n`;
    if (existsSync(path)) {
      appendFileSync(path, chunk, "utf-8");
    } else {
      writeFileSync(path, chunk.replace(/^\n/, ""), "utf-8");
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
