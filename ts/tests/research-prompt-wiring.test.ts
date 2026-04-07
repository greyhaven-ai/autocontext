import { describe, expect, it } from "vitest";
import { ResearchResult, Citation } from "../src/research/types.js";
import { ResearchBrief } from "../src/research/consultation.js";
import { ResearchPromptInjector } from "../src/research/prompt-wiring.js";

function brief(n = 2, confidence = 0.8): ResearchBrief {
  const results = Array.from({ length: n }, (_, i) =>
    new ResearchResult({
      queryTopic: `topic-${i}`,
      summary: `Finding about topic-${i}`,
      confidence,
      citations: [new Citation({ source: `source-${i}`, url: `https://example.com/${i}`, relevance: 0.9 })],
    })
  );
  return ResearchBrief.fromResults("Build API", results);
}

describe("ResearchPromptInjector", () => {
  it("formats brief as section", () => {
    const injector = new ResearchPromptInjector();
    const section = injector.formatBrief(brief());
    expect(section).toContain("External Research");
    expect(section).toContain("topic-0");
    expect(section).toContain("topic-1");
  });

  it("empty brief returns empty", () => {
    const injector = new ResearchPromptInjector();
    expect(injector.formatBrief(ResearchBrief.empty("test"))).toBe("");
  });

  it("respects char budget", () => {
    const injector = new ResearchPromptInjector({ maxChars: 500 });
    const section = injector.formatBrief(brief(20));
    expect(section.length).toBeLessThanOrEqual(550);
  });

  it("highest confidence first", () => {
    const results = [
      new ResearchResult({ queryTopic: "low", summary: "Low", confidence: 0.3 }),
      new ResearchResult({ queryTopic: "high", summary: "High", confidence: 0.9 }),
      new ResearchResult({ queryTopic: "mid", summary: "Mid", confidence: 0.6 }),
    ];
    const b = ResearchBrief.fromResults("test", results);
    const section = new ResearchPromptInjector().formatBrief(b);
    expect(section.indexOf("high")).toBeLessThan(section.indexOf("low"));
  });

  it("inject with placeholder", () => {
    const injector = new ResearchPromptInjector();
    const result = injector.inject("You are helpful.\n\n{research}\n\nHelp.", brief());
    expect(result).toContain("External Research");
    expect(result).toContain("Help.");
  });

  it("inject without placeholder appends", () => {
    const injector = new ResearchPromptInjector();
    const result = injector.inject("You are helpful.", brief());
    expect(result).toMatch(/^You are helpful\./);
    expect(result).toContain("External Research");
  });

  it("inject empty brief returns base", () => {
    const injector = new ResearchPromptInjector();
    expect(injector.inject("Base prompt.", ResearchBrief.empty("x"))).toBe("Base prompt.");
  });

  it("citation formatting", () => {
    const section = new ResearchPromptInjector().formatBrief(brief(1));
    expect(section).toContain("source-0");
    expect(section).toContain("https://example.com/0");
  });
});
