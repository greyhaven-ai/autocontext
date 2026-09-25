import Database from "better-sqlite3";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  getStoreCalibrationExamples,
  getStoreHumanFeedback,
  insertStoreHumanFeedback,
} from "../src/storage/storage-human-feedback-facade.js";
import { migrateDatabase } from "../src/storage/storage-migration-workflow.js";

const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

describe("storage human feedback facade", () => {
  let dir: string;
  let db: Database.Database;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-storage-human-feedback-facade-"));
    db = new Database(join(dir, "test.db"));
    migrateDatabase(db, MIGRATIONS_DIR);
  });

  afterEach(() => {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("preserves human feedback insert, read, and calibration semantics", () => {
    const id = insertStoreHumanFeedback(db, "scenario", "output", 0.4, "needs work", "gen-1");
    expect(id).toBeGreaterThan(0);

    insertStoreHumanFeedback(db, "scenario", "second", null, "notes only");
    insertStoreHumanFeedback(db, "scenario", "third", 0.8, "strong response");

    expect(() => insertStoreHumanFeedback(db, "scenario", "bad", 1.5)).toThrow(
      "human_score must be in [0.0, 1.0], got 1.5",
    );

    const feedback = getStoreHumanFeedback(db, "scenario");
    expect(feedback).toHaveLength(3);
    // Identify the record rather than indexing: created_at has second
    // granularity, so all three rows tie and only the id tiebreak orders them.
    const withGeneration = feedback.find((row) => row.agent_output === "output");
    expect(withGeneration?.generation_id).toBe("gen-1");

    const calibration = getStoreCalibrationExamples(db, "scenario");
    expect(calibration.map((row) => row.agent_output)).toContain("output");
    expect(calibration.map((row) => row.agent_output)).toContain("third");
    expect(calibration.map((row) => row.agent_output)).not.toContain("second");
  });
});
