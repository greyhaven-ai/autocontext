import { describe, expect, it } from "vitest";
import {
  ResearchQuery,
  ResearchResult,
  Citation,
  ResearchConfig,
  type ResearchAdapter,
} from "../src/research/types.js";
import { ResearchEnabledSession } from "../src/research/runtime.js";
import { ResearchBrief, ResearchConsultant } from "../src/research/consultation.js";

class StubAdapter implements ResearchAdapter {
  results: Map<string, ResearchResult>;
  queriesReceived: string[] = [];
  constructor(results?: Map<string, ResearchResult>) {
    this.results = results ?? new Map();
  }
  search(query: ResearchQuery): ResearchResult {
    this.queriesReceived.push(query.topic);
    return this.results.get(query.topic) ?? new ResearchResult({
      queryTopic: query.topic, summary: `Default: ${query.topic}`, confidence: 0.5,
    });
  }
}

function makeResult(topic: string, confidence = 0.8, citations: Citation[] = []): ResearchResult {
  return new ResearchResult({ queryTopic: topic, summary: `Research on ${topic}`, confidence, citations });
}

describe("ResearchBrief", () => {
  it("from results", () => {
    const brief = ResearchBrief.fromResults("Build auth", [makeResult("OAuth2", 0.9), makeResult("JWT", 0.7)]);
    expect(brief.findings).toHaveLength(2);
    expect(brief.avgConfidence).toBeCloseTo(0.8, 1);
  });

  it("filters low confidence", () => {
    const brief = ResearchBrief.fromResults("test", [makeResult("good", 0.8), makeResult("weak", 0.1)], 0.3);
    expect(brief.findings).toHaveLength(1);
  });

  it("deduplicates citations", () => {
    const shared = new Citation({ source: "RFC", url: "https://rfc.example.com", relevance: 0.9 });
    const brief = ResearchBrief.fromResults("test", [
      makeResult("q1", 0.8, [shared, new Citation({ source: "A", relevance: 0.7 })]),
      makeResult("q2", 0.8, [shared, new Citation({ source: "B", relevance: 0.6 })]),
    ]);
    expect(brief.uniqueCitations).toHaveLength(3);
  });

  it("renders markdown", () => {
    const brief = ResearchBrief.fromResults("Build auth", [
      makeResult("OAuth2", 0.9, [new Citation({ source: "RFC 6749", url: "https://example.com", relevance: 0.9 })]),
    ]);
    const md = brief.toMarkdown();
    expect(md).toContain("OAuth2");
    expect(md).toContain("RFC 6749");
  });

  it("empty brief", () => {
    const brief = ResearchBrief.empty("none");
    expect(brief.findings).toHaveLength(0);
    expect(brief.avgConfidence).toBe(0);
  });
});

describe("ResearchConsultant", () => {
  it("consults with topics", () => {
    const adapter = new StubAdapter();
    const session = ResearchEnabledSession.create({ goal: "Build API", adapter });
    const consultant = new ResearchConsultant();
    const brief = consultant.consult(session, ["OAuth2", "token storage"]);
    expect(brief.findings).toHaveLength(2);
    expect(adapter.queriesReceived).toHaveLength(2);
  });

  it("respects budget", () => {
    const adapter = new StubAdapter();
    const config = new ResearchConfig({ enabled: true, maxQueriesPerSession: 1 });
    const session = ResearchEnabledSession.create({ goal: "test", adapter, config });
    const consultant = new ResearchConsultant();
    const brief = consultant.consult(session, ["t1", "t2", "t3"]);
    expect(brief.findings).toHaveLength(1);
  });

  it("no adapter returns empty", () => {
    const session = ResearchEnabledSession.create({ goal: "test" });
    const consultant = new ResearchConsultant();
    const brief = consultant.consult(session, ["anything"]);
    expect(brief.findings).toHaveLength(0);
  });

  it("filters by min confidence", () => {
    const results = new Map<string, ResearchResult>([
      ["good", makeResult("good", 0.9)],
      ["weak", makeResult("weak", 0.1)],
    ]);
    const adapter = new StubAdapter(results);
    const session = ResearchEnabledSession.create({ goal: "test", adapter });
    const consultant = new ResearchConsultant({ minConfidence: 0.3 });
    const brief = consultant.consult(session, ["good", "weak"]);
    expect(brief.findings).toHaveLength(1);
  });
});
