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
        meanScore: 0.65,
        bestScore: 0.7,
        elo: 1050,
        wins: 3,
        losses: 2,
        gateDecision: "advance",
        status: "completed",
      });
      store.appendAgentOutput("run-1", 1, "competitor", '{"aggression":0.6}');
      store.recordMatch("run-1", 1, {
        seed: 42,
        score: 0.7,
        passedValidation: true,
        validationErrors: "",
        winner: "challenger",
      });
      store.upsertGeneration("run-1", 2, {
        meanScore: 0.55,
        bestScore: 0.6,
        elo: 1020,
        wins: 2,
        losses: 3,
        gateDecision: "rollback",
        status: "completed",
      });
      store.appendAgentOutput("run-1", 2, "competitor", '{"aggression":0.9}');

      expect(resolveTrainingExportRuns(store, { runId: "run-1" })).toEqual([
        { run_id: "run-1", scenario: "grid_ctf" },
      ]);
      expect(resolveTrainingExportRuns(store, { scenario: "grid_ctf" })).toEqual([
        { run_id: "run-1", scenario: "grid_ctf" },
      ]);
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

  it("carries evaluator_epoch from the generation row onto the exported record", () => {
    const dir = mkdtempSync(join(tmpdir(), "ac-export-epoch-"));
    try {
      const store = new SQLiteStore(join(dir, "test.db"));
      store.migrate(join(process.cwd(), "migrations"));
      const artifacts = new ArtifactStore({
        runsRoot: join(dir, "runs"),
        knowledgeRoot: join(dir, "knowledge"),
      });

      store.createRun("run-1", "grid_ctf", 1, "local");
      store.upsertGeneration("run-1", 1, {
        meanScore: 0.9,
        bestScore: 0.9,
        elo: 1000,
        wins: 1,
        losses: 0,
        gateDecision: "advance",
        status: "completed",
        evaluatorEpoch: "e-1",
      });
      store.appendAgentOutput("run-1", 1, "competitor", '{"aggression":0.5}');

      const records = buildTrainingExportRecordsForRun({
        store,
        artifacts,
        run: { run_id: "run-1", scenario: "grid_ctf" },
      });

      expect(records).toHaveLength(1);
      expect(records[0]).toMatchObject({ evaluator_epoch: "e-1" });

      store.close();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("excludes quarantined generations by default and includes them with the flag", () => {
    const dir = mkdtempSync(join(tmpdir(), "ac-export-quarantine-"));
    try {
      const store = new SQLiteStore(join(dir, "test.db"));
      store.migrate(join(process.cwd(), "migrations"));
      const artifacts = new ArtifactStore({
        runsRoot: join(dir, "runs"),
        knowledgeRoot: join(dir, "knowledge"),
      });

      store.createRun("run-1", "grid_ctf", 2, "local");
      store.upsertGeneration("run-1", 1, {
        meanScore: 0.9,
        bestScore: 0.9,
        elo: 0,
        wins: 0,
        losses: 0,
        gateDecision: "completed",
        status: "completed",
        evaluatorEpoch: "e-2",
        quarantined: 1,
      });
      store.appendAgentOutput("run-1", 1, "competitor", '{"aggression":0.5}');
      store.upsertGeneration("run-1", 2, {
        meanScore: 0.8,
        bestScore: 0.8,
        elo: 0,
        wins: 0,
        losses: 0,
        gateDecision: "advance",
        status: "completed",
        evaluatorEpoch: "e-1",
      });
      store.appendAgentOutput("run-1", 2, "competitor", '{"aggression":0.9}');

      const run = { run_id: "run-1", scenario: "grid_ctf" };
      const defaultRecords = buildTrainingExportRecordsForRun({ store, artifacts, run });
      expect(
        defaultRecords.map((r) => (r as { generation_index: number }).generation_index),
      ).toEqual([2]);

      const allRecords = buildTrainingExportRecordsForRun({
        store,
        artifacts,
        run,
        includeQuarantined: true,
      });
      expect(
        allRecords.map((r) => (r as { generation_index: number }).generation_index).sort(),
      ).toEqual([1, 2]);

      store.close();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("excludes a quarantined generation's training record but keeps its matches", () => {
    const dir = mkdtempSync(join(tmpdir(), "ac-export-quarantine-matches-"));
    try {
      const store = new SQLiteStore(join(dir, "test.db"));
      store.migrate(join(process.cwd(), "migrations"));
      const artifacts = new ArtifactStore({
        runsRoot: join(dir, "runs"),
        knowledgeRoot: join(dir, "knowledge"),
      });

      store.createRun("run-1", "grid_ctf", 1, "local");
      store.upsertGeneration("run-1", 1, {
        meanScore: 0.9,
        bestScore: 0.9,
        elo: 0,
        wins: 0,
        losses: 0,
        gateDecision: "completed",
        status: "completed",
        quarantined: 1,
      });
      store.appendAgentOutput("run-1", 1, "competitor", '{"aggression":0.5}');
      store.recordMatch("run-1", 1, {
        seed: 7,
        score: 0.5,
        passedValidation: true,
        validationErrors: "",
      });

      const records = buildTrainingExportRecordsForRun({
        store,
        artifacts,
        run: { run_id: "run-1", scenario: "grid_ctf" },
        includeMatches: true,
      });

      const training = records.filter((r) => "gate_decision" in r);
      const matches = records.filter((r) => "seed" in r);
      expect(training).toHaveLength(0); // quarantined generation's training record excluded
      expect(matches).toHaveLength(1); // its tournament match survives
      expect(matches[0]).toMatchObject({ generation_index: 1, seed: 7 });

      store.close();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
