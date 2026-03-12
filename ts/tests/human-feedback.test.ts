import { describe, it, expect, beforeEach } from "vitest";
import { mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { SQLiteStore } from "../src/storage/index.js";

const MIGRATIONS_DIR = join(import.meta.dirname, "..", "migrations");

function createStore(): SQLiteStore {
  const dir = mkdtempSync(join(tmpdir(), "autocontext-feedback-"));
  const store = new SQLiteStore(join(dir, "test.db"));
  store.migrate(MIGRATIONS_DIR);
  return store;
}

describe("Human Feedback Storage", () => {
  let store: SQLiteStore;

  beforeEach(() => {
    store = createStore();
  });

  it("insert and retrieve feedback", () => {
    const rowId = store.insertHumanFeedback("test_task", "some output", 0.3, "missed the point");
    expect(rowId).toBeGreaterThan(0);

    const items = store.getHumanFeedback("test_task");
    expect(items).toHaveLength(1);
    expect(items[0].human_score).toBe(0.3);
    expect(items[0].human_notes).toBe("missed the point");
    expect(items[0].agent_output).toBe("some output");
  });

  it("returns empty for nonexistent scenario", () => {
    expect(store.getHumanFeedback("nonexistent")).toEqual([]);
  });

  it("stores multiple feedback entries", () => {
    store.insertHumanFeedback("s1", "out1", 0.2, "bad");
    store.insertHumanFeedback("s1", "out2", 0.8, "good");
    store.insertHumanFeedback("s2", "out3", 0.5, "ok");

    expect(store.getHumanFeedback("s1")).toHaveLength(2);
    expect(store.getHumanFeedback("s2")).toHaveLength(1);
  });

  it("rejects scores outside [0, 1]", () => {
    expect(() => store.insertHumanFeedback("s", "out", 1.5)).toThrow();
    expect(() => store.insertHumanFeedback("s", "out", -0.1)).toThrow();
  });

  it("allows null score", () => {
    store.insertHumanFeedback("s1", "out", null, "just notes");
    const items = store.getHumanFeedback("s1");
    expect(items[0].human_score).toBeNull();
    expect(items[0].human_notes).toBe("just notes");
  });

  it("stores generation_id when provided", () => {
    store.insertHumanFeedback("s1", "out", 0.5, "notes", "gen-123");
    const items = store.getHumanFeedback("s1");
    expect(items[0].generation_id).toBe("gen-123");
  });

  it("respects limit", () => {
    for (let i = 0; i < 10; i++) {
      store.insertHumanFeedback("s1", `out${i}`, 0.5, `note${i}`);
    }
    expect(store.getHumanFeedback("s1", 3)).toHaveLength(3);
  });
});

describe("Calibration Examples", () => {
  let store: SQLiteStore;

  beforeEach(() => {
    store = createStore();
  });

  it("only returns entries with both score and notes", () => {
    // Score only, no notes
    store.insertHumanFeedback("s1", "out1", 0.5, "");
    // Notes only, no score
    store.insertHumanFeedback("s1", "out2", null, "some notes");
    // Both score and notes
    store.insertHumanFeedback("s1", "out3", 0.3, "bad output");

    const calibration = store.getCalibrationExamples("s1");
    expect(calibration).toHaveLength(1);
    expect(calibration[0].agent_output).toBe("out3");
  });

  it("respects limit", () => {
    for (let i = 0; i < 10; i++) {
      store.insertHumanFeedback("s1", `out${i}`, 0.5, `note${i}`);
    }
    expect(store.getCalibrationExamples("s1", 3)).toHaveLength(3);
  });

  it("returns entries that have both score and notes", () => {
    store.insertHumanFeedback("s1", "complete1", 0.2, "first feedback");
    store.insertHumanFeedback("s1", "complete2", 0.8, "second feedback");

    const calibration = store.getCalibrationExamples("s1");
    expect(calibration).toHaveLength(2);
    // Both entries have score + notes, so both are returned
    const outputs = calibration.map(c => c.agent_output);
    expect(outputs).toContain("complete1");
    expect(outputs).toContain("complete2");
  });
});
