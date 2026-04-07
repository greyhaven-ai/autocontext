import { describe, expect, it, beforeEach } from "vitest";
import { mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { ResearchResult, Citation } from "../src/research/types.js";
import { ResearchBrief } from "../src/research/consultation.js";
import { ResearchStore } from "../src/research/persistence.js";

function makeBrief(goal = "test", n = 2): ResearchBrief {
  const results = Array.from({ length: n }, (_, i) =>
    new ResearchResult({
      queryTopic: `topic-${i}`,
      summary: `Summary ${i}`,
      confidence: 0.5 + i * 0.1,
      citations: [new Citation({ source: `src-${i}`, url: `https://example.com/${i}`, relevance: 0.8 })],
    })
  );
  return ResearchBrief.fromResults(goal, results);
}

describe("ResearchStore", () => {
  let store: ResearchStore;

  beforeEach(() => {
    store = new ResearchStore(mkdtempSync(join(tmpdir(), "research-")));
  });

  it("save and load brief", () => {
    const brief = makeBrief("Build auth API");
    const ref = store.saveBrief("s1", brief);
    expect(ref.sessionId).toBe("s1");
    const loaded = store.loadBrief(ref.briefId);
    expect(loaded).not.toBeNull();
    expect(loaded!.goal).toBe("Build auth API");
    expect(loaded!.findings).toHaveLength(2);
  });

  it("list briefs by session", () => {
    store.saveBrief("s1", makeBrief("a"));
    store.saveBrief("s1", makeBrief("b"));
    store.saveBrief("s2", makeBrief("c"));
    expect(store.listBriefs("s1")).toHaveLength(2);
    expect(store.listBriefs("s2")).toHaveLength(1);
  });

  it("nonexistent returns null", () => {
    expect(store.loadBrief("nope")).toBeNull();
  });

  it("persists across instances", () => {
    const dir = mkdtempSync(join(tmpdir(), "research-"));
    const s1 = new ResearchStore(dir);
    const ref = s1.saveBrief("s1", makeBrief("persistent"));
    const s2 = new ResearchStore(dir);
    expect(s2.loadBrief(ref.briefId)!.goal).toBe("persistent");
  });

  it("citations round trip", () => {
    const ref = store.saveBrief("s1", makeBrief("cite", 1));
    const loaded = store.loadBrief(ref.briefId)!;
    expect(loaded.uniqueCitations).toHaveLength(1);
    expect(loaded.uniqueCitations[0].source).toBe("src-0");
  });

  it("brief count", () => {
    expect(store.briefCount()).toBe(0);
    store.saveBrief("s1", makeBrief());
    store.saveBrief("s1", makeBrief());
    expect(store.briefCount()).toBe(2);
  });

  it("delete brief", () => {
    const ref = store.saveBrief("s1", makeBrief());
    expect(store.deleteBrief(ref.briefId)).toBe(true);
    expect(store.loadBrief(ref.briefId)).toBeNull();
    expect(store.briefCount()).toBe(0);
  });
});
