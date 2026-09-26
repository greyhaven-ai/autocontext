import { describe, expect, it } from "vitest";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

import { HookBus, HookEvents } from "../src/extensions/index.js";
import { asRunId, asScenarioName } from "../src/domain/ids.js";
import { ArtifactStore } from "../src/knowledge/artifact-store.js";

function makeRoot(): string {
  return mkdtempSync(join(tmpdir(), "autoctx-artifact-hooks-"));
}

function storeWithPathHook(root: string, rewrite: (path: string) => string): ArtifactStore {
  const bus = new HookBus();
  bus.on(HookEvents.ARTIFACT_WRITE, (event) => ({ path: rewrite(String(event.payload.path)) }));
  return new ArtifactStore({
    runsRoot: join(root, "runs"),
    knowledgeRoot: join(root, "knowledge"),
    hookBus: bus,
  });
}

function renameInPlace(path: string): string {
  return join(dirname(path), "renamed.md");
}

function linkScenarioDir(root: string, target: string): void {
  mkdirSync(target, { recursive: true });
  mkdirSync(join(root, "knowledge"), { recursive: true });
  symlinkSync(target, join(root, "knowledge", "grid_ctf"), "dir");
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

      store.writePlaybook(asScenarioName("grid_ctf"), "base playbook");
      store.appendDeadEnd(asScenarioName("grid_ctf"), "base dead end");
      store.appendCompactionEntries(asRunId("run-1"), [{
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

      const result = store.appendCompactionEntries(asRunId("run-1"), [{
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

  it.each([
    ["an existing directory", true],
    ["a missing directory", false],
  ])("rejects artifact_write rewrites that escape the managed root through a symlink to %s", (_label, createTarget) => {
    const root = makeRoot();
    try {
      const victim = join(root, "victim");
      if (createTarget) {
        mkdirSync(victim);
      }
      mkdirSync(join(root, "runs", "run-1"), { recursive: true });
      symlinkSync(victim, join(root, "runs", "run-1", "escape"), "dir");
      const store = storeWithPathHook(root, () => join(root, "runs", "run-1", "escape", "out.md"));

      expect(() => store.writeMarkdown(join(root, "runs", "run-1", "out.md"), "content"))
        .toThrow(/artifact_write path must stay within the original managed root/);
      expect(existsSync(join(victim, "out.md"))).toBe(false);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("rejects artifact_write rewrites through a symlinked scenario directory outside the managed roots", () => {
    const root = makeRoot();
    try {
      const shared = join(root, "shared", "grid_ctf");
      linkScenarioDir(root, shared);
      const store = storeWithPathHook(root, renameInPlace);

      expect(() => store.writeMarkdown(join(root, "knowledge", "grid_ctf", "notes.md"), "content"))
        .toThrow(/artifact_write path must stay within the original managed root/);
      expect(readdirSync(shared)).toEqual([]);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("keeps unchanged artifact_write paths through a symlinked scenario directory", () => {
    const root = makeRoot();
    try {
      const shared = join(root, "shared", "grid_ctf");
      linkScenarioDir(root, shared);
      const store = storeWithPathHook(root, (path) => path);

      store.writeMarkdown(join(root, "knowledge", "grid_ctf", "notes.md"), "content");

      expect(readFileSync(join(shared, "notes.md"), "utf-8")).toBe("content\n");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("accepts artifact_write rewrites through a symlink that leads into another managed root", () => {
    const root = makeRoot();
    try {
      const linked = join(root, "runs", "shared_grid");
      linkScenarioDir(root, linked);
      const store = storeWithPathHook(root, renameInPlace);

      store.writeMarkdown(join(root, "knowledge", "grid_ctf", "notes.md"), "content");

      expect(readFileSync(join(linked, "renamed.md"), "utf-8")).toBe("content\n");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
