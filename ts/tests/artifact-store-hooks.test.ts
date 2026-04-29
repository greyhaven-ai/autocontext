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
});
