import { describe, expect, it } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import { ArtifactStore } from "../src/knowledge/artifact-store.js";
import { SQLiteStore } from "../src/storage/index.js";
import {
  buildTrainingExportRecordsForRun,
  resolveTrainingExportRuns,
} from "../src/training/export-records-workflow.js";

describe("training export records workflow", () => {
  it("resolves runs and emits per-generation records with keptOnly and includeMatches", async () => {
    const dir = mkdtempSync(join(tmpdir(), "ac-export-records-"));
    try {
      const store = new SQLiteStore(join(dir, "test.db"));
      store.migrate(join(process.cwd(), "migrations"));
      const artifacts = new ArtifactStore({
        runsRoot: join(dir, "runs"),
        knowledgeRoot: join(dir, "knowledge"),
      });

      artifacts.writePlaybook("grid_ctf", "# Strategy\n");
      store.createRun("run-1", "grid_ctf", 2, "local");
      store.upsertGeneration("run-1", 1, {
        meanScore: 0.65, bestScore: 0.7, elo: 1050,
        wins: 3, losses: 2, gateDecision: "advance", status: "completed",
      });
      store.appendAgentOutput("run-1", 1, "competitor", '{"aggression":0.6}');
      store.recordMatch("run-1", 1, { seed: 42, score: 0.7, passedValidation: true, validationErrors: "", winner: "challenger" });
      store.upsertGeneration("run-1", 2, {
        meanScore: 0.55, bestScore: 0.6, elo: 1020,
        wins: 2, losses: 3, gateDecision: "rollback", status: "completed",
      });
      store.appendAgentOutput("run-1", 2, "competitor", '{"aggression":0.9}');

      expect(resolveTrainingExportRuns(store, { runId: "run-1" })).toEqual([{ run_id: "run-1", scenario: "grid_ctf" }]);
      expect(resolveTrainingExportRuns(store, { scenario: "grid_ctf" })).toEqual([{ run_id: "run-1", scenario: "grid_ctf" }]);
      expect(resolveTrainingExportRuns(store, {})).toEqual([]);

      const generationEvents: Array<{ generationIndex: number; recordCount: number }> = [];
      const records = buildTrainingExportRecordsForRun({
        store,
        artifacts,
        run: { run_id: "run-1", scenario: "grid_ctf" },
        keptOnly: true,
        includeMatches: true,
        onGenerationRecords: (generationIndex, generationRecords) => {
          generationEvents.push({ generationIndex, recordCount: generationRecords.length });
        },
      });

      expect(records).toHaveLength(2);
      expect(records[0]).toMatchObject({
        run_id: "run-1",
        generation_index: 1,
        score: 0.7,
        gate_decision: "advance",
      });
      expect(records[1]).toMatchObject({
        seed: 42,
        passed_validation: true,
      });
      expect(generationEvents).toEqual([{ generationIndex: 1, recordCount: 2 }]);

      store.close();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
