import Database from "better-sqlite3";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  getCalibrationExampleRecords,
  getHumanFeedbackRecords,
  insertHumanFeedbackRecord,
} from "../src/storage/human-feedback-store.js";
import { migrateDatabase } from "../src/storage/storage-migration-workflow.js";

const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

describe("human feedback store workflow", () => {
  let dir: string;
  let db: Database.Database;

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), "ac-human-feedback-store-"));
    db = new Database(join(dir, "test.db"));
    migrateDatabase(db, MIGRATIONS_DIR);
  });

  afterEach(() => {
    db.close();
    rmSync(dir, { recursive: true, force: true });
  });

  it("inserts, reads, validates, and filters calibration examples", () => {
    const id = insertHumanFeedbackRecord(db, "scenario", "output", 0.4, "needs work", "gen-1");
    expect(id).toBeGreaterThan(0);

    insertHumanFeedbackRecord(db, "scenario", "second", null, "notes only");
    insertHumanFeedbackRecord(db, "scenario", "third", 0.8, "strong response");
    db.prepare(`INSERT INTO human_feedback(scenario_name, agent_output, human_score, human_notes, acquisition_id)
                VALUES (?, ?, ?, ?, ?)`).run("scenario", "acquired", 0.9, "human review", "acq-1");

    expect(() => insertHumanFeedbackRecord(db, "scenario", "bad", 1.5)).toThrow(
      "human_score must be in [0.0, 1.0], got 1.5",
    );

    const feedback = getHumanFeedbackRecords(db, "scenario");
    expect(feedback).toHaveLength(4);
    // Identify the record rather than indexing into the result: `created_at` has
    // second granularity, so all three rows tie and position is meaningless
    // except for the id tiebreak asserted in the ordering tests below.
    const withGeneration = feedback.find((row) => row.agent_output === "output");
    expect(withGeneration?.generation_id).toBe("gen-1");

    const calibration = getCalibrationExampleRecords(db, "scenario");
    expect(calibration.map((row) => row.agent_output)).toContain("output");
    expect(calibration.map((row) => row.agent_output)).toContain("third");
    expect(calibration.map((row) => row.agent_output)).not.toContain("second");
    expect(calibration.map((row) => row.agent_output)).not.toContain("acquired");
  });

  it("returns the newest records, not an arbitrary page, when created_at ties", () => {
    // `created_at` defaults to datetime('now'), which has second granularity
    // (migrations/008_human_feedback.sql), so a burst of writes shares one value.
    // Without a tiebreak, `ORDER BY created_at DESC LIMIT n` returned the OLDEST
    // n rows and hid the newest ones entirely.
    const ids: number[] = [];
    for (let i = 1; i <= 12; i += 1) {
      ids.push(insertHumanFeedbackRecord(db, "scenario", `out-${i}`, 0.5, "notes"));
    }

    const distinct = new Set(
      getHumanFeedbackRecords(db, "scenario", 100).map((row) => row.created_at),
    );
    expect(distinct.size).toBe(1);

    const page = getHumanFeedbackRecords(db, "scenario", 10);
    expect(page).toHaveLength(10);
    expect(page.map((row) => row.id)).toEqual([...ids].reverse().slice(0, 10));
  });

  it("returns the newest calibration examples when created_at ties", () => {
    const ids: number[] = [];
    for (let i = 1; i <= 8; i += 1) {
      ids.push(insertHumanFeedbackRecord(db, "scenario", `cal-${i}`, 0.7, "scored and noted"));
    }

    const page = getCalibrationExampleRecords(db, "scenario", 5);
    expect(page).toHaveLength(5);
    expect(page.map((row) => row.id)).toEqual([...ids].reverse().slice(0, 5));
  });
});
