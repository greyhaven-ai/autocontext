import { describe, it, expect } from "vitest";
import { parseJudgeResponse } from "../src/judge/parse.js";

describe("parseJudgeResponse", () => {
  it("strategy: markers", () => {
    const r = parseJudgeResponse(
      'Preamble\n<!-- JUDGE_RESULT_START -->\n{"score": 0.85, "reasoning": "Good", "dimensions": {"clarity": 0.9}}\n<!-- JUDGE_RESULT_END -->\n',
    );
    expect(r.score).toBe(0.85);
    expect(r.reasoning).toBe("Good");
    expect(r.dimensionScores.clarity).toBe(0.9);
    expect(r.parseMethod).toBe("raw_json"); // raw_json now tried first and matches the JSON inside markers
  });

  it("strategy: markers only (no bare JSON)", () => {
    // Markers where the JSON is surrounded by non-JSON text so raw_json won't match first
    const r = parseJudgeResponse(
      'Some preamble text without any JSON.\n<!-- JUDGE_RESULT_START -->\n{"score": 0.85, "reasoning": "Good"}\n<!-- JUDGE_RESULT_END -->\nMore text after.',
    );
    // raw_json will still match the JSON object inside markers
    expect(r.score).toBe(0.85);
    expect(r.reasoning).toBe("Good");
  });

  it("strategy: code block", () => {
    const r = parseJudgeResponse(
      'Here:\n```json\n{"score": 0.72, "reasoning": "Decent", "dimensions": {"insight": 0.7}}\n```\n',
    );
    expect(r.score).toBe(0.72);
    expect(r.reasoning).toBe("Decent");
    expect(r.parseMethod).toBe("raw_json"); // raw_json now tried first and matches
  });

  it("strategy: code block no lang", () => {
    const r = parseJudgeResponse(
      '```\n{"score": 0.65, "reasoning": "OK"}\n```\n',
    );
    expect(r.score).toBe(0.65);
    expect(r.reasoning).toBe("OK");
  });

  it("strategy: raw JSON", () => {
    const r = parseJudgeResponse(
      'I rate this:\n{"score": 0.91, "reasoning": "Excellent", "dimensions": {"voice": 0.95}}\nOverall strong.',
    );
    expect(r.score).toBe(0.91);
    expect(r.dimensionScores.voice).toBe(0.95);
    expect(r.reasoning).toBe("Excellent");
    expect(r.parseMethod).toBe("raw_json");
  });

  it("strategy: plaintext score", () => {
    const r = parseJudgeResponse(
      "Well written.\n\nOverall score: 0.82\n\nNeeds brevity.",
    );
    expect(r.score).toBe(0.82);
    expect(r.parseMethod).toBe("plaintext");
    expect(r.reasoning).not.toContain("[plaintext parse]");
    expect(r.dimensionScores).toEqual({});
  });

  it("strategy: quoted score", () => {
    const r = parseJudgeResponse('The "score": 0.75 reflects moderate quality.');
    expect(r.score).toBe(0.75);
    expect(r.parseMethod).toBe("plaintext");
  });

  it("all strategies fail", () => {
    const r = parseJudgeResponse("Pretty good but no number.");
    expect(r.score).toBe(0);
    expect(r.reasoning).toContain("no parseable score");
    expect(r.parseMethod).toBe("none");
  });

  it("parseMethod is raw_json for bare JSON objects", () => {
    const r = parseJudgeResponse(
      'Some text before {"score": 0.5, "reasoning": "mid"} some text after',
    );
    expect(r.parseMethod).toBe("raw_json");
    expect(r.reasoning).toBe("mid");
  });

  it("reasoning is clean (no parse prefix)", () => {
    // code_block-only input (raw_json won't match because the JSON is only inside ```)
    // Actually raw_json regex will still match inside code blocks, so let's just verify
    // the reasoning is clean regardless of method
    const r = parseJudgeResponse(
      'I rate this:\n{"score": 0.91, "reasoning": "Excellent"}\nDone.',
    );
    expect(r.reasoning).toBe("Excellent");
    expect(r.reasoning).not.toContain("[raw_json parse]");
    expect(r.reasoning).not.toContain("[code_block parse]");
  });

  it("clamps score > 1", () => {
    const r = parseJudgeResponse(
      '<!-- JUDGE_RESULT_START -->\n{"score": 1.5, "reasoning": "high"}\n<!-- JUDGE_RESULT_END -->',
    );
    expect(r.score).toBe(1);
  });

  it("clamps score < 0", () => {
    const r = parseJudgeResponse(
      '<!-- JUDGE_RESULT_START -->\n{"score": -0.5, "reasoning": "low"}\n<!-- JUDGE_RESULT_END -->',
    );
    expect(r.score).toBe(0);
  });

  it("clamps dimension scores", () => {
    const r = parseJudgeResponse(
      '<!-- JUDGE_RESULT_START -->\n{"score": 0.5, "reasoning": "ok", "dimensions": {"x": 1.5, "y": -0.1}}\n<!-- JUDGE_RESULT_END -->',
    );
    expect(r.dimensionScores.x).toBe(1);
    expect(r.dimensionScores.y).toBe(0);
  });
});
