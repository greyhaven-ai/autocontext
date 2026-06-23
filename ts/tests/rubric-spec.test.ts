import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  LLMJudge,
  compileRubricSpec,
  legacyRubricSpec,
  lintRubricSpec,
  proposeRubricPatches,
  RubricSpecSchema,
  type CompiledRubric,
} from "../src/judge/index.js";
import type { CompletionResult, LLMProvider } from "../src/types/index.js";

const repoRoot = join(import.meta.dirname, "..", "..");
const fixtures = JSON.parse(
  readFileSync(join(repoRoot, "docs", "rubric-spec-parity-fixtures.json"), "utf-8"),
) as {
  fixtures: Record<string, unknown>;
  expected: Record<
    string,
    { compiled_summary?: Record<string, unknown>; finding_codes?: string[] }
  >;
};

class MockProvider implements LLMProvider {
  readonly name = "mock";
  readonly userPrompts: string[] = [];

  constructor(private readonly responseText: string) {}

  async complete(opts: {
    systemPrompt: string;
    userPrompt: string;
    model?: string;
    temperature?: number;
    maxTokens?: number;
  }): Promise<CompletionResult> {
    this.userPrompts.push(opts.userPrompt);
    return { text: this.responseText, model: opts.model ?? "mock-v1", usage: {} };
  }

  defaultModel(): string {
    return "mock-v1";
  }
}

function summary(compiled: CompiledRubric): Record<string, unknown> {
  return compiled.toSummary();
}

describe("RubricSpec contract", () => {
  it("wraps legacy string rubrics as one overall criterion", () => {
    const spec = legacyRubricSpec(fixtures.fixtures.legacy_string as string);

    expect(spec.rubric_id).toBe("legacy-string-rubric");
    expect(spec.criteria[0].id).toBe("overall");
    expect(summary(compileRubricSpec(spec))).toEqual(
      fixtures.expected.legacy_string.compiled_summary,
    );
  });

  it.each(["multi_criterion_numeric", "binary_disqualifier", "scoped_corpus"])(
    "compiles %s to the shared expected summary",
    (fixtureName) => {
      const spec = RubricSpecSchema.parse(fixtures.fixtures[fixtureName]);

      expect(summary(compileRubricSpec(spec))).toEqual(
        fixtures.expected[fixtureName].compiled_summary,
      );
    },
  );

  it("lints invalid rubrics before live judge calls", () => {
    const spec = RubricSpecSchema.parse(fixtures.fixtures.invalid_lint_warnings);
    const codes = lintRubricSpec(spec)
      .map((finding) => finding.code)
      .sort();

    expect(codes).toEqual(fixtures.expected.invalid_lint_warnings.finding_codes);
    expect(() => compileRubricSpec(spec)).toThrow(/invalid rubric/);
  });

  it("rejects source-schema invalid missing scale fields", () => {
    const missingScaleId = JSON.parse(JSON.stringify(fixtures.fixtures.multi_criterion_numeric)) as {
      criteria: Array<Record<string, unknown>>;
    };
    delete missingScaleId.criteria[0].scale_id;
    const badScaleKind = JSON.parse(JSON.stringify(fixtures.fixtures.multi_criterion_numeric)) as {
      scales: Array<Record<string, unknown>>;
    };
    badScaleKind.scales[0].kind = "ordinal";

    expect(() => RubricSpecSchema.parse(missingScaleId)).toThrow(/scale_id/);
    expect(() => RubricSpecSchema.parse(badScaleKind)).toThrow(/kind/);
  });

  it("uses typed criterion ids as judge dimensions", async () => {
    const spec = RubricSpecSchema.parse(fixtures.fixtures.multi_criterion_numeric);
    const provider = new MockProvider(
      "<!-- JUDGE_RESULT_START -->\n" +
        '{"score":0.7,"reasoning":"ok","dimensions":{"correctness":0.8,"evidence":0.6,"invented":1}}\n' +
        "<!-- JUDGE_RESULT_END -->",
    );

    const judge = new LLMJudge({ model: "mock-v1", rubric: spec, provider });
    const result = await judge.evaluate({ taskPrompt: "Answer", agentOutput: "Output" });

    expect(result.dimensionScores).toEqual({ correctness: 0.8, evidence: 0.6, clarity: 0 });
    expect(result.dimensionsWereGenerated).toBe(false);
    expect(provider.userPrompts[0]).toContain("Required Dimensions");
    expect(provider.userPrompts[0]).toContain("correctness, evidence, clarity");
  });

  it("keeps rubric patch proposals experimental and structurally safe", () => {
    const spec = RubricSpecSchema.parse(fixtures.fixtures.multi_criterion_numeric);
    const anchors = [
      {
        criterion_id: "correctness",
        human_score: 0.2,
        judge_score: 0.8,
        human_notes: "Penalize unsupported claims",
      },
      {
        criterion_id: "correctness",
        human_score: 0.3,
        judge_score: 0.7,
        human_notes: "Require direct evidence",
      },
      { criterion_id: "clarity", human_score: 0.9, judge_score: 0.88, human_notes: "Clear enough" },
    ];

    expect(() => proposeRubricPatches(spec, anchors)).toThrow(/experimental/);

    const proposal = proposeRubricPatches(spec, anchors, { experimental: true });

    expect(proposal.requires_human_review).toBe(true);
    expect(proposal.patches[0].path).toBe("/criteria/correctness/description");
    expect(proposal.patches[0].op).toBe("append");
    expect(proposal.patches.map((patch) => patch.path).join(" ")).not.toContain("criterion_id");
    expect(proposal.metrics.agreement).toBeLessThan(1);
    expect(proposal.metrics.discrimination).toBeGreaterThan(0);
  });
});
