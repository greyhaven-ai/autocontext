import { describe, expect, it } from "vitest";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { HookBus, HookEvents } from "../src/extensions/index.js";
import { ArtifactStore } from "../src/knowledge/artifact-store.js";

function makeRoot(): string {
  return mkdtempSync(join(tmpdir(), "autoctx-artifact-hooks-"));
}

describe("ArtifactStore extension hooks", () => {
  it("lets artifact_write hooks mutate markdown content inside managed roots", () => {
    const root = makeRoot();
    try {
      const bus = new HookBus();
      bus.on(HookEvents.ARTIFACT_WRITE, (event) => ({
        content: `${event.payload.content}\nmutated by hook`,
      }));
      const store = new ArtifactStore({
        runsRoot: join(root, "runs"),
        knowledgeRoot: join(root, "knowledge"),
        hookBus: bus,
      });
      const path = join(root, "runs", "run-1", "out.md");

      store.writeMarkdown(path, "original");

      expect(readFileSync(path, "utf-8")).toBe("original\nmutated by hook\n");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("rejects artifact_write path rewrites outside the original managed root", () => {
    const root = makeRoot();
    try {
      const bus = new HookBus();
      bus.on(HookEvents.ARTIFACT_WRITE, () => ({
        path: join(root, "outside.md"),
      }));
      const store = new ArtifactStore({
        runsRoot: join(root, "runs"),
        knowledgeRoot: join(root, "knowledge"),
        hookBus: bus,
      });

      expect(() => store.writeMarkdown(join(root, "runs", "run-1", "out.md"), "content"))
        .toThrow(/artifact_write path must stay within the original managed root/);
      expect(existsSync(join(root, "outside.md"))).toBe(false);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("applies artifact_write hooks to playbooks, dead ends, and compaction ledgers", () => {
    const root = makeRoot();
    try {
      const bus = new HookBus();
      const seen: string[] = [];
      bus.on(HookEvents.ARTIFACT_WRITE, (event) => {
        const path = String(event.payload.path ?? "");
        seen.push(`${event.payload.format}:${path.slice(path.lastIndexOf("/") + 1)}`);
        if (path.endsWith("playbook.md")) {
          return { content: `${event.payload.content}\nplaybook hook` };
        }
        if (path.endsWith("dead_ends.md")) {
          return { content: `${event.payload.content}\ndead-end hook` };
        }
        return undefined;
      });
      const store = new ArtifactStore({
        runsRoot: join(root, "runs"),
        knowledgeRoot: join(root, "knowledge"),
        hookBus: bus,
      });

      store.writePlaybook("grid_ctf", "base playbook");
      store.appendDeadEnd("grid_ctf", "base dead end");
      store.appendCompactionEntries("run-1", [{
        id: "entry-1",
        parentId: "",
        timestamp: "2026-04-29T00:00:00.000Z",
        summary: "summary",
        firstKeptEntryId: "turn-1",
        tokensBefore: 100,
      }]);

      expect(readFileSync(join(root, "knowledge", "grid_ctf", "playbook.md"), "utf-8"))
        .toContain("playbook hook");
      expect(readFileSync(join(root, "knowledge", "grid_ctf", "dead_ends.md"), "utf-8"))
        .toContain("dead-end hook");
      expect(seen).toEqual(expect.arrayContaining([
        "markdown:playbook.md",
        "markdown:dead_ends.md",
        "jsonl:compactions.jsonl",
        "text:compactions.latest",
      ]));
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("returns hook-mutated compaction entries and paths after ledger writes", () => {
    const root = makeRoot();
    try {
      const bus = new HookBus();
      const runsRoot = join(root, "runs");
      const redactedLedgerPath = join(runsRoot, "run-1", "redacted", "compactions.jsonl");
      const redactedLatestPath = join(runsRoot, "run-1", "redacted", "compactions.latest");
      const redactedEntry = {
        id: "redacted-entry",
        parentId: "",
        timestamp: "2026-04-29T00:00:00.000Z",
        summary: "redacted summary",
        firstKeptEntryId: "redacted-kept",
        tokensBefore: 5,
        details: { component: "redacted_component" },
      };
      bus.on(HookEvents.ARTIFACT_WRITE, (event) => {
        const path = String(event.payload.path ?? "");
        if (path.endsWith("compactions.jsonl")) {
          return {
            path: redactedLedgerPath,
            content: `${JSON.stringify(redactedEntry)}\n`,
          };
        }
        if (path.endsWith("compactions.latest")) {
          return { path: redactedLatestPath };
        }
        return undefined;
      });
      const store = new ArtifactStore({
        runsRoot,
        knowledgeRoot: join(root, "knowledge"),
        hookBus: bus,
      });

      const result = store.appendCompactionEntries("run-1", [{
        id: "original-entry",
        parentId: "",
        timestamp: "2026-04-29T00:00:00.000Z",
        summary: "secret-bearing summary",
        firstKeptEntryId: "original-kept",
        tokensBefore: 100,
        details: { component: "session_reports" },
      }]);

      expect(result).toMatchObject({
        ledgerPath: redactedLedgerPath,
        latestEntryPath: redactedLatestPath,
        latestEntryId: "redacted-entry",
        entries: [{
          id: "redacted-entry",
          summary: "redacted summary",
          details: { component: "redacted_component" },
        }],
      });
      expect(readFileSync(redactedLedgerPath, "utf-8")).toContain("redacted summary");
      expect(readFileSync(redactedLedgerPath, "utf-8")).not.toContain("secret-bearing summary");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
