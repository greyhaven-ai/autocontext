import { describe, expect, it } from "vitest";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { mkdtempSync } from "node:fs";

import { ArtifactStore } from "../src/knowledge/artifact-store.js";
import { SQLiteStore } from "../src/storage/index.js";
import type { GenerationJournalAttempt, GenerationJournalScenario } from "../src/loop/generation-journal.js";
import { GenerationJournal } from "../src/loop/generation-journal.js";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-generation-journal-"));
}

function makeScenario(): GenerationJournalScenario {
  return {
    name: "journal_scenario",
    replayToNarrative(replay: Array<Record<string, unknown>>) {
      return `events=${replay.length}`;
    },
  };
}

function makeAttempt(): GenerationJournalAttempt {
  return {
    competitorPrompt: "Describe your strategy",
    competitorResultText: '{"alpha":0.7}',
    strategy: { alpha: 0.7 },
    gateDecision: "advance",
    tournamentResult: {
      meanScore: 0.7,
      bestScore: 0.9,
      wins: 2,
      losses: 1,
      elo: 1012,
      matches: [
        {
          seed: 1000,
          score: 0.9,
          winner: "challenger",
          passedValidation: true,
          validationErrors: [],
          replay: [{ event: "best" }],
        },
        {
          seed: 1001,
          score: 0.5,
          winner: "incumbent",
          passedValidation: true,
          validationErrors: [],
          replay: [{ event: "other" }],
        },
      ],
    },
  };
}

describe("GenerationJournal", () => {
  it("persists generation records and replay artifacts", () => {
    const dir = makeTempDir();
    try {
      const dbPath = join(dir, "test.db");
      const runsRoot = join(dir, "runs");
      const knowledgeRoot = join(dir, "knowledge");
      const store = new SQLiteStore(dbPath);
      store.migrate(join(__dirname, "..", "migrations"));
      store.createRun("run-1", "journal_scenario", 1, "local");

      const artifacts = new ArtifactStore({ runsRoot, knowledgeRoot });
      const journal = new GenerationJournal({
        store,
        artifacts,
        scenario: makeScenario(),
      });

      journal.persistGeneration("run-1", 1, makeAttempt());

      expect(store.getGenerations("run-1")).toHaveLength(1);
      expect(store.getMatchesForRun("run-1")).toHaveLength(2);
      expect(store.getAgentOutputs("run-1", 1)).toHaveLength(1);

      const replayPath = join(runsRoot, "run-1", "generations", "gen_1", "replays", "journal_scenario_1.json");
      expect(existsSync(replayPath)).toBe(true);
      expect(readFileSync(replayPath, "utf-8")).toContain("events=1");

      const summaryPath = join(runsRoot, "run-1", "generations", "gen_1", "tournament_summary.json");
      expect(readFileSync(summaryPath, "utf-8")).toContain('"gate_decision": "advance"');

      store.close();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("writes session reports and counts dead ends", () => {
    const dir = makeTempDir();
    try {
      const dbPath = join(dir, "test.db");
      const runsRoot = join(dir, "runs");
      const knowledgeRoot = join(dir, "knowledge");
      const store = new SQLiteStore(dbPath);
      store.migrate(join(__dirname, "..", "migrations"));
      store.createRun("run-2", "journal_scenario", 2, "local");
      store.upsertGeneration("run-2", 1, {
        meanScore: 0.4,
        bestScore: 0.5,
        elo: 1001,
        wins: 1,
        losses: 1,
        gateDecision: "advance",
        status: "completed",
      });

      const artifacts = new ArtifactStore({ runsRoot, knowledgeRoot });
      artifacts.appendDeadEnd("journal_scenario", "avoid tunnel vision");
      artifacts.appendDeadEnd("journal_scenario", "avoid overconfidence");

      const journal = new GenerationJournal({
        store,
        artifacts,
        scenario: makeScenario(),
      });

      expect(journal.countDeadEnds()).toBe(2);

      const reportPath = journal.persistSessionReport("run-2", {
        runStartedAtMs: Date.now() - 2_000,
        explorationMode: "linear",
      });

      expect(existsSync(reportPath)).toBe(true);
      expect(readFileSync(reportPath, "utf-8")).toContain("journal_scenario");

      const knowledgeReportPath = join(knowledgeRoot, "journal_scenario", "session_reports", "run-2.md");
      expect(existsSync(knowledgeReportPath)).toBe(true);

      store.close();
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
