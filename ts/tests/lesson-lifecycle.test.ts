import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "ac-lifecycle-"));
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

async function mods() {
  return {
    ...(await import("../src/knowledge/lesson-lifecycle.js")),
    ...(await import("../src/knowledge/lessons.js")),
  };
}

function writeDeadEnds(scenario: string, body: string): void {
  mkdirSync(join(dir, scenario), { recursive: true });
  writeFileSync(join(dir, scenario, "dead_ends.md"), body, "utf-8");
}

describe("lesson lifecycle (single store)", () => {
  it("buildLifecycle buckets active/stale/pending/deadEnd from one store", async () => {
    const { buildLifecycle, LessonStore, makeMeta } = await mods();
    const store = new LessonStore(dir);
    store.writeLessons("scn", [
      {
        id: "a",
        text: "fresh",
        meta: makeMeta({ generation: 20, bestScore: 0.5, lastValidatedGen: 20 }),
      },
      {
        id: "b",
        text: "old",
        meta: makeMeta({ generation: 2, bestScore: 0.5, lastValidatedGen: 2 }),
      },
      {
        id: "p",
        text: "held",
        meta: makeMeta({ generation: 20, bestScore: 0, approvalStatus: "pending" }),
      },
    ]);
    writeDeadEnds(
      "scn",
      "# Dead-End Registry\n\n- **Gen 3**: tried Y (score=0.1000) — regressed\n",
    );

    const view = buildLifecycle({ knowledgeRoot: dir, scenario: "scn", currentGeneration: 20 });
    expect(view.active.map((l: { text: string }) => l.text)).toEqual(["fresh"]);
    expect(view.stale.map((l: { text: string }) => l.text)).toEqual(["old"]);
    expect(view.pending.map((l: { text: string }) => l.text)).toEqual(["held"]);
    expect(view.deadEnd.length).toBe(1);
    expect(view.deadEnd[0].text).toContain("tried Y");
  });

  it("approve flips pending to active; idempotent; never lowers validation generation", async () => {
    const { approveLesson, LessonStore, makeMeta } = await mods();
    const store = new LessonStore(dir);
    store.writeLessons("scn", [
      {
        id: "p",
        text: "held",
        meta: makeMeta({
          generation: 20,
          bestScore: 0,
          lastValidatedGen: 20,
          approvalStatus: "pending",
        }),
      },
    ]);
    // current_generation 0 (e.g. derived from an otherwise-empty store) must not lower it.
    expect(
      approveLesson({ knowledgeRoot: dir, scenario: "scn", lessonId: "p", currentGeneration: 0 }),
    ).toBe("active");
    const lesson = store.readLessons("scn")[0];
    expect(lesson.meta.approvalStatus).toBe("active");
    expect(lesson.meta.lastValidatedGen).toBe(20);
    // now active, not pending -> null
    expect(
      approveLesson({ knowledgeRoot: dir, scenario: "scn", lessonId: "p", currentGeneration: 9 }),
    ).toBeNull();
  });

  it("reject removes pending only; does not delete an active lesson", async () => {
    const { rejectLesson, LessonStore, makeMeta } = await mods();
    const store = new LessonStore(dir);
    store.writeLessons("scn", [
      {
        id: "x",
        text: "held",
        meta: makeMeta({ generation: 5, bestScore: 0, approvalStatus: "pending" }),
      },
      { id: "a", text: "active one", meta: makeMeta({ generation: 5, bestScore: 0 }) },
    ]);
    expect(rejectLesson({ knowledgeRoot: dir, scenario: "scn", lessonId: "x" })).toBe(true);
    expect(rejectLesson({ knowledgeRoot: dir, scenario: "scn", lessonId: "a" })).toBe(false);
    expect(store.readLessons("scn").map((l) => l.text)).toEqual(["active one"]);
  });

  it("curate marks stale / moves to deadEnd / deletes; 404-equivalent on missing", async () => {
    const { curateLesson, LessonStore, makeMeta } = await mods();
    const store = new LessonStore(dir);
    store.writeLessons("scn", [
      { id: "s", text: "stale me", meta: makeMeta({ generation: 5, bestScore: 0 }) },
    ]);
    expect(
      curateLesson({
        knowledgeRoot: dir,
        scenario: "scn",
        lessonId: "s",
        action: "stale",
        currentGeneration: 9,
      }),
    ).toBe("stale");
    expect(store.readLessons("scn")[0].meta.lastValidatedGen).toBe(-1);

    store.writeLessons("scn", [
      { id: "d", text: "dead me", meta: makeMeta({ generation: 5, bestScore: 0 }) },
    ]);
    expect(
      curateLesson({
        knowledgeRoot: dir,
        scenario: "scn",
        lessonId: "d",
        action: "deadEnd",
        currentGeneration: 9,
      }),
    ).toBe("deadEnd");
    expect(store.readLessons("scn")).toEqual([]);

    store.writeLessons("scn", [
      { id: "z", text: "gone", meta: makeMeta({ generation: 5, bestScore: 0 }) },
    ]);
    expect(
      curateLesson({
        knowledgeRoot: dir,
        scenario: "scn",
        lessonId: "z",
        action: "delete",
        currentGeneration: 9,
      }),
    ).toBe("deleted");
    expect(store.readLessons("scn")).toEqual([]);

    expect(
      curateLesson({
        knowledgeRoot: dir,
        scenario: "scn",
        lessonId: "nope",
        action: "delete",
        currentGeneration: 9,
      }),
    ).toBeNull();
  });

  it("pending lessons are excluded from getApplicableLessons", async () => {
    const { LessonStore, makeMeta } = await mods();
    const { isApplicable } = await import("../src/knowledge/lessons.js");
    const store = new LessonStore(dir);
    const pending = {
      id: "p",
      text: "UNAPPROVED",
      meta: makeMeta({ generation: 20, bestScore: 0, approvalStatus: "pending" }),
    };
    const active = {
      id: "a",
      text: "approved",
      meta: makeMeta({ generation: 20, bestScore: 0, lastValidatedGen: 20 }),
    };
    store.writeLessons("scn", [pending, active]);
    expect(isApplicable(pending, 20, 10)).toBe(false);
    expect(isApplicable(active, 20, 10)).toBe(true);
  });
});
