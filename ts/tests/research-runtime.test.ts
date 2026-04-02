import { describe, expect, it } from "vitest";
import {
  ResearchQuery,
  ResearchResult,
  ResearchConfig,
  type ResearchAdapter,
} from "../src/research/types.js";
import { ResearchEnabledSession } from "../src/research/runtime.js";

class StubAdapter implements ResearchAdapter {
  callCount = 0;
  search(query: ResearchQuery): ResearchResult {
    this.callCount++;
    return new ResearchResult({
      queryTopic: query.topic,
      summary: `Stub: ${query.topic}`,
      confidence: 0.8,
    });
  }
}

describe("ResearchEnabledSession", () => {
  it("accepts adapter", () => {
    const session = ResearchEnabledSession.create({ goal: "test", adapter: new StubAdapter() });
    expect(session.hasResearch).toBe(true);
    expect(session.researchQueriesUsed).toBe(0);
  });

  it("no adapter", () => {
    const session = ResearchEnabledSession.create({ goal: "test" });
    expect(session.hasResearch).toBe(false);
  });

  it("research query", () => {
    const adapter = new StubAdapter();
    const session = ResearchEnabledSession.create({ goal: "test", adapter });
    const result = session.research(new ResearchQuery({ topic: "auth" }));
    expect(result).not.toBeNull();
    expect(result!.summary).toContain("auth");
    expect(session.researchQueriesUsed).toBe(1);
    expect(adapter.callCount).toBe(1);
  });

  it("no adapter returns null", () => {
    const session = ResearchEnabledSession.create({ goal: "test" });
    expect(session.research(new ResearchQuery({ topic: "x" }))).toBeNull();
  });

  it("respects budget", () => {
    const adapter = new StubAdapter();
    const config = new ResearchConfig({ enabled: true, maxQueriesPerSession: 2 });
    const session = ResearchEnabledSession.create({ goal: "test", adapter, config });
    session.research(new ResearchQuery({ topic: "q1" }));
    session.research(new ResearchQuery({ topic: "q2" }));
    expect(session.research(new ResearchQuery({ topic: "q3" }))).toBeNull();
    expect(session.researchQueriesUsed).toBe(2);
  });

  it("emits events", () => {
    const session = ResearchEnabledSession.create({ goal: "test", adapter: new StubAdapter() });
    session.research(new ResearchQuery({ topic: "auth" }));
    expect(session.events.some((e) => e.eventType === "research_requested")).toBe(true);
  });

  it("accumulates history", () => {
    const session = ResearchEnabledSession.create({ goal: "test", adapter: new StubAdapter() });
    session.research(new ResearchQuery({ topic: "q1" }));
    session.research(new ResearchQuery({ topic: "q2" }));
    expect(session.researchHistory).toHaveLength(2);
  });
});
