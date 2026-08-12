/**
 * AC-937: the TypeScript judge scores the same responses as the Python one.
 *
 * The twin of `autocontext/tests/test_judge_score_parsing.py`, deliberately
 * case-for-case, because the defect it covers existed in BOTH engines and only
 * Python's was fixed (AC-924). `parseJudgeResponse` is on the production path
 * via `judge/llm-judge.ts`, so this was a live scoring bug and not a latent one.
 *
 * A mis-parsed judge score is not a crash. It is a wrong number entering the
 * loop's ranking, so the run continues and reports a result nobody can tell is
 * wrong. That is why these assert on scores rather than on which tier fired.
 */
import { describe, expect, it } from "vitest";

import { parseJudgeResponse } from "../src/judge/parse.js";

const RESULT_START = "<!-- JUDGE_RESULT_START -->";
const RESULT_END = "<!-- JUDGE_RESULT_END -->";
const FENCE = "```";

describe("judge score parsing", () => {
  it.each([
    ["markers win outright", `${RESULT_START}\n{"score":0.9}\n${RESULT_END}`, 0.9, "markers"],
    [
      "markers beat stray prose JSON",
      `Aside {"score":0.15}\n${RESULT_START}\n{"score":0.85}\n${RESULT_END}`,
      0.85,
      "markers",
    ],
    ["bare object", '{"score": 0.6}', 0.6, "raw_json"],
    ["fenced object", `${FENCE}json\n{"score": 0.8}\n${FENCE}`, 0.8, "raw_json"],
    ["untagged fence", `${FENCE}\n{"score": 0.7}\n${FENCE}`, 0.7, "raw_json"],
    ["prose then object", 'Verdict:\n{"score": 0.55}', 0.55, "raw_json"],
    ["plain text score", "Overall score: 0.45", 0.45, "plaintext"],
    ["x / 1.0 form", "I would rate this 0.35 / 1.0", 0.35, "plaintext"],
    ["nothing scoreable", "This response cannot be scored.", 0, "none"],
  ])("parses %s as recorded", (_label, response, score, method) => {
    const parsed = parseJudgeResponse(response as string);
    expect([parsed.score, parsed.parseMethod]).toEqual([score, method]);
  });

  it.each([
    [
      "prose draft loses to the fenced answer",
      `Draft thought {"score": 0.1}\n${FENCE}json\n{"score": 0.95}\n${FENCE}`,
      0.95,
      0.1,
    ],
    [
      "a reasoning block loses to the answer",
      `${FENCE}\nthinking out loud, maybe {"score": 0.05}\n${FENCE}\n${FENCE}json\n{"score": 0.88}\n${FENCE}`,
      0.88,
      0.05,
    ],
  ])(
    "no longer lets a discarded draft outrank the answer: %s",
    (label, response, expected, was) => {
      // The second case is the one that bit in practice. Emitting a reasoning
      // block before the answer is ordinary open-weight output, and it scored the
      // run 0.05 where the judge said 0.88.
      const parsed = parseJudgeResponse(response as string);
      expect(parsed.score).toBe(expected);
      expect(parsed.score).not.toBe(was);
      void label;
    },
  );

  it("reads arbitrary nesting depth as JSON rather than scraping it", () => {
    // Was: two nesting levels defeated the old regex, there was no fence to
    // help, and a well-formed object got scraped by a plaintext pattern that
    // found the score by luck and dropped every dimension.
    const parsed = parseJudgeResponse('{"score": 0.64, "dimensions": {"a": {"b": 1}}}');
    expect([parsed.score, parsed.parseMethod]).toEqual([0.64, "raw_json"]);
  });

  it("skips an unrelated object before the scored verdict", () => {
    const parsed = parseJudgeResponse(
      '{"metadata": {"request_id": "abc"}}\n' +
        '{"score": 1e-1, "reasoning": "final", "dimensions": {"quality": 0.77}}',
    );
    expect(parsed).toEqual({
      score: 0.1,
      reasoning: "final",
      dimensionScores: { quality: 0.77 },
      parseMethod: "raw_json",
    });
  });

  it("still takes the first of two bare objects in prose", () => {
    // NOT fixed, deliberately. With no fence and no markers there is nothing to
    // tell a draft from a verdict. Recorded so the limit is known rather than
    // assumed away by the cases above.
    const parsed = parseJudgeResponse('I considered {"score": 0.2} but settled on {"score": 0.9}');
    expect([parsed.score, parsed.parseMethod]).toEqual([0.2, "raw_json"]);
  });

  it("keeps dimensions from a singly-nested payload", () => {
    const parsed = parseJudgeResponse('{"score": 0.75, "dimensions": {"a": 1}}');
    expect(parsed.dimensionScores).toEqual({ a: 1 });
  });

  it("falls through when the marker block is not valid JSON", () => {
    const parsed = parseJudgeResponse(`${RESULT_START}\nnot json\n${RESULT_END}\n{"score": 0.42}`);
    expect([parsed.score, parsed.parseMethod]).toEqual([0.42, "raw_json"]);
  });
});
