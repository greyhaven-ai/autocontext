import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "ac-pending-"));
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

async function mod() {
  return {
    ...(await import("../src/knowledge/pending-lessons.js")),
    ...(await import("../src/knowledge/lessons.js")),
  };
}

describe("PendingLessonStore", () => {
  it("add/read roundtrip", async () => {
    const { PendingLessonStore, makeMeta } = await mod();
    const store = new PendingLessonStore(dir);
    store.add("scn", { id: "a", text: "held", meta: makeMeta({ generation: 1, bestScore: 0 }) });
    expect(store.read("scn").map((l) => l.text)).toEqual(["held"]);
  });

  it("dedupes by id or text", async () => {
    const { PendingLessonStore, makeMeta } = await mod();
    const store = new PendingLessonStore(dir);
    const m = makeMeta({ generation: 1, bestScore: 0 });
    store.add("scn", { id: "a", text: "x", meta: m });
    store.add("scn", { id: "b", text: "x", meta: m });
    store.add("scn", { id: "a", text: "y", meta: m });
    expect(store.read("scn").length).toBe(1);
  });

  it("remove returns the entry, persists, idempotent", async () => {
    const { PendingLessonStore, makeMeta } = await mod();
    const store = new PendingLessonStore(dir);
    store.add("scn", { id: "a", text: "held", meta: makeMeta({ generation: 1, bestScore: 0 }) });
    expect(store.remove("scn", "a")?.text).toBe("held");
    expect(store.read("scn")).toEqual([]);
    expect(store.remove("scn", "a")).toBeNull();
  });
});
