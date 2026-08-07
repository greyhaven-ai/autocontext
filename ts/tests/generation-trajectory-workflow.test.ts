import Database from "better-sqlite3";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { SQLiteStore, type GenerationRow } from "../src/storage/index.js";
import {
  createRunRecord,
  upsertGenerationRecord,
} from "../src/storage/generation-record-store.js";
import {
  getScoreTrajectoryRecords,
  parseDimensionSummaryJson,
} from "../src/storage/generation-trajectory-workflow.js";
import { asDbPath } from "../src/domain/ids.js";

const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

describe("generation trajectory workflow", () => {
  let dir: string;
  let db: Database.Database;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-generation-trajectory-"));
    const dbPath = join(dir, "test.db");
    const store = new SQLiteStore(asDbPath(dbPath));
    store.migrate(MIGRATIONS_DIR);
    store.close();
    db = new Database(dbPath);
  });

  afterEach(() => {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("parses dimension summaries defensively and computes deltas across completed generations", () => {
    expect(parseDimensionSummaryJson(null)).toEqual({});
    expect(parseDimensionSummaryJson("not json")).toEqual({});
    expect(parseDimensionSummaryJson('{"clarity":0.8}')).toEqual({ clarity: 0.8 });

    createRunRecord(db, "run-1", "grid_ctf", 3, "local");
    upsertGenerationRecord(db, "run-1", 1, {
      meanScore: 0.4,
      bestScore: 0.5,
      elo: 1000,
      wins: 2,
      losses: 3,
      gateDecision: "retry",
      status: "completed",
      dimensionSummaryJson: '{"clarity":0.8}',
    });
    upsertGenerationRecord(db, "run-1", 2, {
      meanScore: 0.6,
      bestScore: 0.7,
      elo: 1050,
      wins: 3,
      losses: 2,
      gateDecision: "advance",
      status: "completed",
      dimensionSummaryJson: "not json",
    });

    const rows = getScoreTrajectoryRecords<GenerationRow>(db, "run-1");
    expect(rows).toHaveLength(2);
    expect(rows[0].delta).toBeCloseTo(0.5);
    expect(rows[0].dimension_summary).toEqual({ clarity: 0.8 });
    expect(rows[1].delta).toBeCloseTo(0.2);
    expect(rows[1].dimension_summary).toEqual({});
  });
});
