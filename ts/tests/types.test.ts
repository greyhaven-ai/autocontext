import { describe, it, expect } from "vitest";
import {
  CompletionResultSchema,
  JudgeResultSchema,
  AgentTaskResultSchema,
  TaskRowSchema,
  RoundResultSchema,
  ImprovementResultSchema,
  NotificationEventSchema,
  ProviderError,
} from "../src/types/index.js";

describe("CompletionResultSchema", () => {
  it("parses minimal result", () => {
    const r = CompletionResultSchema.parse({ text: "hello" });
    expect(r.text).toBe("hello");
    expect(r.usage).toEqual({});
    expect(r.model).toBeUndefined();
  });

  it("parses full result", () => {
    const r = CompletionResultSchema.parse({
      text: "hi",
      model: "gpt-4",
      usage: { input: 10, output: 5 },
      costUsd: 0.01,
    });
    expect(r.model).toBe("gpt-4");
    expect(r.costUsd).toBe(0.01);
  });
});

describe("JudgeResultSchema", () => {
  it("parses with defaults", () => {
    const r = JudgeResultSchema.parse({ score: 0.85, reasoning: "good" });
    expect(r.dimensionScores).toEqual({});
    expect(r.rawResponses).toEqual([]);
  });

  it("rejects score > 1", () => {
    expect(() => JudgeResultSchema.parse({ score: 1.5, reasoning: "" })).toThrow();
  });

  it("rejects score < 0", () => {
    expect(() => JudgeResultSchema.parse({ score: -0.1, reasoning: "" })).toThrow();
  });
});

describe("AgentTaskResultSchema", () => {
  it("parses with dimensions", () => {
    const r = AgentTaskResultSchema.parse({
      score: 0.7,
      reasoning: "ok",
      dimensionScores: { clarity: 0.8 },
    });
    expect(r.dimensionScores.clarity).toBe(0.8);
  });
});

describe("RoundResultSchema", () => {
  it("defaults judgeFailed to false", () => {
    const r = RoundResultSchema.parse({
      roundNumber: 1,
      output: "text",
      score: 0.5,
      reasoning: "ok",
    });
    expect(r.judgeFailed).toBe(false);
    expect(r.isRevision).toBe(false);
  });
});

describe("ImprovementResultSchema", () => {
  it("defaults judgeFailures to 0", () => {
    const r = ImprovementResultSchema.parse({
      rounds: [],
      bestOutput: "",
      bestScore: 0,
      bestRound: 1,
      totalRounds: 0,
      metThreshold: false,
    });
    expect(r.judgeFailures).toBe(0);
  });
});

describe("NotificationEventSchema", () => {
  it("parses threshold_met event", () => {
    const r = NotificationEventSchema.parse({
      eventType: "threshold_met",
      taskId: "t1",
      specName: "spec",
      score: 0.9,
      threshold: 0.8,
      message: "done",
    });
    expect(r.eventType).toBe("threshold_met");
  });

  it("rejects invalid event type", () => {
    expect(() =>
      NotificationEventSchema.parse({
        eventType: "invalid",
        taskId: "t1",
        specName: "spec",
        score: 0.5,
        message: "x",
      }),
    ).toThrow();
  });
});

describe("ProviderError", () => {
  it("has correct name", () => {
    const e = new ProviderError("test");
    expect(e.name).toBe("ProviderError");
    expect(e.message).toBe("test");
    expect(e instanceof Error).toBe(true);
  });
});
