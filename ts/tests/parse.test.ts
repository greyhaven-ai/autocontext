import { describe, it, expect } from "vitest";
import { parseJudgeResponse } from "../src/judge/parse.js";

describe("parseJudgeResponse", () => {
  it("strategy 1: markers", () => {
    const r = parseJudgeResponse(
      'Preamble\n<!-- JUDGE_RESULT_START -->\n{"score": 0.85, "reasoning": "Good", "dimensions": {"clarity": 0.9}}\n<!-- JUDGE_RESULT_END -->\n',
    );
    expect(r.score).toBe(0.85);
    expect(r.reasoning).toBe("Good");
    expect(r.dimensionScores.clarity).toBe(0.9);
  });

  it("strategy 2: code block", () => {
    const r = parseJudgeResponse(
      'Here:\n```json\n{"score": 0.72, "reasoning": "Decent", "dimensions": {"insight": 0.7}}\n```\n',
    );
    expect(r.score).toBe(0.72);
    expect(r.reasoning).toContain("[code_block parse]");
    expect(r.reasoning).toContain("Decent");
  });

  it("strategy 2: code block no lang", () => {
    const r = parseJudgeResponse(
      '```\n{"score": 0.65, "reasoning": "OK"}\n```\n',
    );
    expect(r.score).toBe(0.65);
  });

  it("strategy 3: raw JSON", () => {
    const r = parseJudgeResponse(
      'I rate this:\n{"score": 0.91, "reasoning": "Excellent", "dimensions": {"voice": 0.95}}\nOverall strong.',
    );
    expect(r.score).toBe(0.91);
    expect(r.dimensionScores.voice).toBe(0.95);
    expect(r.reasoning).toContain("[raw_json parse]");
  });

  it("strategy 4: plaintext score", () => {
    const r = parseJudgeResponse(
      "Well written.\n\nOverall score: 0.82\n\nNeeds brevity.",
    );
    expect(r.score).toBe(0.82);
    expect(r.reasoning).toContain("[plaintext parse]");
    expect(r.dimensionScores).toEqual({});
  });

  it("strategy 4: quoted score", () => {
    const r = parseJudgeResponse('The "score": 0.75 reflects moderate quality.');
    expect(r.score).toBe(0.75);
  });

  it("all strategies fail", () => {
    const r = parseJudgeResponse("Pretty good but no number.");
    expect(r.score).toBe(0);
    expect(r.reasoning).toContain("no parseable score");
  });

  it("markers win over code block", () => {
    const r = parseJudgeResponse(
      '```json\n{"score": 0.50, "reasoning": "code"}\n```\n' +
        '<!-- JUDGE_RESULT_START -->\n{"score": 0.90, "reasoning": "markers"}\n<!-- JUDGE_RESULT_END -->',
    );
    expect(r.score).toBe(0.9);
    expect(r.reasoning).toBe("markers");
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
