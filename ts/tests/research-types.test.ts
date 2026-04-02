import { describe, expect, it } from "vitest";
import {
  ResearchQuery,
  Citation,
  ResearchResult,
  ResearchConfig,
  Urgency,
} from "../src/research/types.js";

describe("ResearchQuery", () => {
  it("creates with defaults", () => {
    const q = new ResearchQuery({ topic: "OAuth2" });
    expect(q.topic).toBe("OAuth2");
    expect(q.urgency).toBe(Urgency.NORMAL);
    expect(q.maxResults).toBe(5);
  });

  it("accepts all fields", () => {
    const q = new ResearchQuery({
      topic: "auth",
      context: "FastAPI app",
      urgency: Urgency.HIGH,
      maxResults: 10,
      constraints: ["peer-reviewed"],
      scenarioFamily: "agent_task",
    });
    expect(q.constraints).toHaveLength(1);
    expect(q.scenarioFamily).toBe("agent_task");
  });
});

describe("Citation", () => {
  it("creates with source and url", () => {
    const c = new Citation({ source: "RFC 6749", url: "https://tools.ietf.org/rfc6749", relevance: 0.9 });
    expect(c.source).toBe("RFC 6749");
  });
});

describe("ResearchResult", () => {
  it("tracks citations", () => {
    const r = new ResearchResult({
      queryTopic: "auth",
      summary: "Use OAuth2",
      confidence: 0.8,
      citations: [new Citation({ source: "RFC", relevance: 0.9 })],
    });
    expect(r.hasCitations).toBe(true);
  });

  it("no citations", () => {
    const r = new ResearchResult({ queryTopic: "test", summary: "s", confidence: 0.5 });
    expect(r.hasCitations).toBe(false);
  });
});

describe("ResearchConfig", () => {
  it("disabled by default", () => {
    const c = new ResearchConfig();
    expect(c.enabled).toBe(false);
    expect(c.maxQueriesPerSession).toBe(20);
  });

  it("accepts overrides", () => {
    const c = new ResearchConfig({ enabled: true, maxQueriesPerSession: 5, minConfidence: 0.5 });
    expect(c.enabled).toBe(true);
    expect(c.maxQueriesPerSession).toBe(5);
  });
});
