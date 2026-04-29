import { describe, expect, it } from "vitest";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { ArtifactStore } from "../src/knowledge/artifact-store.js";
import type { CompactionEntry } from "../src/knowledge/compaction-ledger.js";

function makeStore(root: string): ArtifactStore {
  return new ArtifactStore({
    runsRoot: join(root, "runs"),
    knowledgeRoot: join(root, "knowledge"),
  });
}

describe("ArtifactStore compaction ledger", () => {
  it("round-trips Pi-shaped entries with a latest-id sidecar", () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-ts-compactions-"));
    try {
      const store = makeStore(root);
      const entries: CompactionEntry[] = [
        {
          type: "compaction",
          id: "aaaa1111",
          parentId: "",
          timestamp: "2026-04-29T17:30:00Z",
          summary: "first",
          firstKeptEntryId: "component:playbook:kept",
          tokensBefore: 120,
          details: { component: "playbook", tokensAfter: 60 },
        },
        {
          type: "compaction",
          id: "bbbb2222",
          parentId: "aaaa1111",
          timestamp: "2026-04-29T17:31:00Z",
          summary: "second",
          firstKeptEntryId: "component:experiment_log:kept",
          tokensBefore: 300,
          details: { component: "experiment_log", tokensAfter: 80 },
        },
      ];

      store.appendCompactionEntries("run-1", entries);

      expect(store.latestCompactionEntryId("run-1")).toBe("bbbb2222");
      expect(store.readCompactionEntries("run-1", { limit: 1 })).toEqual([
        expect.objectContaining({ id: "bbbb2222", firstKeptEntryId: "component:experiment_log:kept" }),
      ]);
      expect(existsSync(store.compactionLatestEntryPath("run-1"))).toBe(true);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("tails legacy ledgers when the latest sidecar is absent", () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-ts-compaction-legacy-"));
    try {
      const store = makeStore(root);
      const ledgerPath = store.compactionLedgerPath("legacy-run");
      mkdirSync(join(root, "runs", "legacy-run"), { recursive: true });
      writeFileSync(
        ledgerPath,
        [
          JSON.stringify({ type: "compaction", id: "old", summary: "old" }),
          JSON.stringify({ type: "compaction", id: "legacy-last", summary: "new" }),
        ].join("\n") + "\n",
        "utf-8",
      );

      expect(store.latestCompactionEntryId("legacy-run")).toBe("legacy-last");
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });

  it("rejects run ids that escape runsRoot", () => {
    const root = mkdtempSync(join(tmpdir(), "autoctx-ts-compaction-safe-"));
    try {
      const store = makeStore(root);

      expect(() => store.compactionLedgerPath("../outside")).toThrow(/run_id.*runs root/i);
      expect(() => store.readCompactionEntries("../outside")).toThrow(/run_id.*runs root/i);
    } finally {
      rmSync(root, { recursive: true, force: true });
    }
  });
});
