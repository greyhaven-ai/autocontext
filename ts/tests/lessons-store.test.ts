import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "ac-lessons-"));
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

describe("LessonStore", () => {
  it("add/read roundtrip and on-disk snake_case schema", async () => {
    const { LessonStore, makeMeta } = await import("../src/knowledge/lessons.js");
    const store = new LessonStore(dir);
    const lesson = store.addLesson("scn", "avoid X", makeMeta({ generation: 3, bestScore: 0.5 }));
    expect(lesson.id).toMatch(/^lesson_/);
    expect(store.readLessons("scn").map((l) => l.text)).toEqual(["avoid X"]);
    const raw = JSON.parse(readFileSync(join(dir, "scn", "lessons.json"), "utf-8"));
    expect(raw[0]).toMatchObject({ id: lesson.id, text: "avoid X" });
    expect(raw[0].meta).toMatchObject({ generation: 3, best_score: 0.5, last_validated_gen: 3 });
  });

  it("read of a missing file returns []", async () => {
    const { LessonStore } = await import("../src/knowledge/lessons.js");
    expect(new LessonStore(dir).readLessons("never")).toEqual([]);
  });

  it("isStale uses last_validated_gen + window; isApplicable excludes stale/superseded", async () => {
    const { LessonStore, makeMeta, isStale, isApplicable } =
      await import("../src/knowledge/lessons.js");
    const store = new LessonStore(dir);
    store.writeLessons("scn", [
      {
        id: "fresh",
        text: "f",
        meta: makeMeta({ generation: 20, bestScore: 0, lastValidatedGen: 20 }),
      },
      {
        id: "old",
        text: "o",
        meta: makeMeta({ generation: 2, bestScore: 0, lastValidatedGen: 2 }),
      },
    ]);
    const lessons = store.readLessons("scn");
    expect(lessons.find((l) => l.id === "fresh")!.meta.lastValidatedGen).toBe(20);
    expect(isStale(lessons.find((l) => l.id === "old")!, 20, 10)).toBe(true);
    expect(isApplicable(lessons.find((l) => l.id === "fresh")!, 20, 10)).toBe(true);
  });

  it("currentGeneration returns max generation/lastValidated", async () => {
    const { LessonStore, makeMeta } = await import("../src/knowledge/lessons.js");
    const store = new LessonStore(dir);
    store.writeLessons("scn", [
      { id: "a", text: "a", meta: makeMeta({ generation: 5, bestScore: 0, lastValidatedGen: 9 }) },
    ]);
    expect(store.currentGeneration("scn")).toBe(9);
    expect(store.currentGeneration("empty")).toBe(0);
  });
});
