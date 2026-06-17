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
    ...(await import("../src/knowledge/pending-lessons.js")),
  };
}

function writeDeadEnds(scenario: string, body: string): void {
  mkdirSync(join(dir, scenario), { recursive: true });
  writeFileSync(join(dir, scenario, "dead_ends.md"), body, "utf-8");
}

describe("lesson lifecycle", () => {
  it("buildLifecycle buckets active/stale/pending/deadEnd", async () => {
    const { buildLifecycle, LessonStore, PendingLessonStore, makeMeta } = await mods();
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
    ]);
    new PendingLessonStore(dir).add("scn", {
      id: "p",
      text: "held",
      meta: makeMeta({ generation: 20, bestScore: 0 }),
    });
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
    expect(view.deadEnd[0].id).toMatch(/^deadend_/);
  });

  it("approve moves pending to active; idempotent", async () => {
    const { approveLesson, LessonStore, PendingLessonStore, makeMeta } = await mods();
    const store = new LessonStore(dir);
    const pending = new PendingLessonStore(dir);
    pending.add("scn", { id: "p", text: "held", meta: makeMeta({ generation: 5, bestScore: 0 }) });
    expect(
      approveLesson({ knowledgeRoot: dir, scenario: "scn", lessonId: "p", currentGeneration: 9 }),
    ).toBe("active");
    expect(store.readLessons("scn").map((l) => l.text)).toEqual(["held"]);
    expect(pending.read("scn")).toEqual([]);
    expect(
      approveLesson({ knowledgeRoot: dir, scenario: "scn", lessonId: "p", currentGeneration: 9 }),
    ).toBeNull();
  });

  it("reject clears pending + active; curate stale/deadEnd/delete", async () => {
    const { rejectLesson, curateLesson, LessonStore, PendingLessonStore, makeMeta } = await mods();
    const store = new LessonStore(dir);
    const pending = new PendingLessonStore(dir);
    store.writeLessons("scn", [
      { id: "x", text: "held", meta: makeMeta({ generation: 5, bestScore: 0 }) },
    ]);
    pending.add("scn", { id: "x", text: "held", meta: makeMeta({ generation: 5, bestScore: 0 }) });
    expect(rejectLesson({ knowledgeRoot: dir, scenario: "scn", lessonId: "x" })).toBe(true);
    expect(store.readLessons("scn")).toEqual([]);
    expect(pending.read("scn")).toEqual([]);

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
});
