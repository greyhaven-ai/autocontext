import { z } from "zod";

export const RUBRIC_SPEC_SCHEMA_VERSION = 1;

type FindingSeverity = "warning" | "error";
const genericWords = new Set(["good", "nice", "appropriate", "adequate", "proper"]);
const hypothesisLoaded = ["prove that", "confirm that", "must show", "obviously"];

export const RubricScopeSchema = z
  .object({
    include: z.array(z.string()).default([]),
    exclude: z.array(z.string()).default([]),
  })
  .default({ include: [], exclude: [] });

export const CorpusProfileSchema = z.object({
  domain: z.string().default(""),
  audience: z.string().default(""),
  source_summary: z.string().default(""),
});

export const RubricScaleSchema = z.object({
  id: z.string(),
  kind: z.enum(["numeric", "binary"]),
  min_score: z.number().default(0),
  max_score: z.number().default(1),
  pass_score: z.number().optional(),
  anchors: z.record(z.string()).default({}),
});

export const RubricCriterionSchema = z.object({
  id: z.string(),
  description: z.string(),
  scale_id: z.string(),
  weight: z.number().default(1),
  scope: RubricScopeSchema.optional(),
  evidence_requirements: z.array(z.string()).default([]),
});

export const RubricDisqualifierSchema = z.object({
  id: z.string(),
  description: z.string(),
});

export const DecisionThresholdsSchema = z.object({
  pass_score: z.number().default(0.8),
  excellent_score: z.number().default(0.9),
});

export const RubricSpecSchema = z.object({
  schema_version: z.literal(1).default(1),
  rubric_id: z.string(),
  title: z.string().default(""),
  goal: z.string(),
  metadata: z.record(z.unknown()).default({}),
  scope: RubricScopeSchema.optional(),
  corpus_profile: CorpusProfileSchema.optional(),
  criteria: z.array(RubricCriterionSchema),
  scales: z.array(RubricScaleSchema),
  disqualifiers: z.array(RubricDisqualifierSchema).default([]),
  evidence_requirements: z.array(z.string()).default([]),
  output_constraints: z.array(z.string()).default([]),
  decision_thresholds: DecisionThresholdsSchema.optional(),
});

export type RubricScope = z.infer<typeof RubricScopeSchema>;
export type CorpusProfile = z.infer<typeof CorpusProfileSchema>;
export type RubricScale = z.infer<typeof RubricScaleSchema>;
export type RubricCriterion = z.infer<typeof RubricCriterionSchema>;
export type RubricDisqualifier = z.infer<typeof RubricDisqualifierSchema>;
export type DecisionThresholds = z.infer<typeof DecisionThresholdsSchema>;
export type RubricSpec = z.infer<typeof RubricSpecSchema>;

export type RubricFinding = {
  code: string;
  severity: FindingSeverity;
  message: string;
  path: string;
};

export class CompiledRubric {
  constructor(
    readonly schema_version: 1,
    readonly rubric_id: string,
    readonly criterion_ids: string[],
    readonly result_dimension_ids: string[],
    readonly scale_ids: string[],
    readonly normalized_weights: Record<string, number>,
    readonly findings: RubricFinding[],
    readonly prompt_contract: string,
  ) {}

  toSummary(): Record<string, unknown> {
    return {
      schema_version: this.schema_version,
      rubric_id: this.rubric_id,
      criterion_ids: [...this.criterion_ids],
      result_dimension_ids: [...this.result_dimension_ids],
      scale_ids: [...this.scale_ids],
      normalized_weights: { ...this.normalized_weights },
      finding_codes: this.findings.map((finding) => finding.code).sort(),
    };
  }
}

export type RubricPatch = {
  op: "append" | "replace";
  path: string;
  value: string;
  reason: string;
};

export type RubricPatchProposal = {
  patches: RubricPatch[];
  requires_human_review: boolean;
  metrics: Record<string, number>;
};

export function legacyRubricSpec(rubric: string): RubricSpec {
  const text = rubric.trim();
  return {
    schema_version: 1,
    rubric_id: "legacy-string-rubric",
    title: "",
    goal: text,
    metadata: {},
    criteria: [{ id: "overall", description: text, scale_id: "score", weight: 1, evidence_requirements: [] }],
    scales: [{ id: "score", kind: "numeric", min_score: 0, max_score: 1, anchors: {} }],
    disqualifiers: [],
    evidence_requirements: [],
    output_constraints: [],
  };
}

export function lintRubricSpec(input: RubricSpec): RubricFinding[] {
  const spec = RubricSpecSchema.parse(input);
  const findings = new Map<string, RubricFinding>();
  const add = (code: string, severity: FindingSeverity, message: string, path = ""): void => {
    if (!findings.has(code)) findings.set(code, { code, severity, message, path });
  };

  if (spec.criteria.length === 0) {
    add("missing_criteria", "error", "RubricSpec must declare at least one criterion", "/criteria");
  }
  if (spec.scales.length === 0) {
    add("missing_scales", "error", "RubricSpec must declare at least one scale", "/scales");
  }

  const criterionCounts = countBy(spec.criteria.map((criterion) => criterion.id));
  for (const [criterionId, count] of Object.entries(criterionCounts)) {
    if (count > 1) {
      add("duplicate_criterion_id", "error", `Duplicate criterion id: ${criterionId}`, `/criteria/${criterionId}`);
    }
  }

  const scaleCounts = countBy(spec.scales.map((scale) => scale.id));
  for (const [scaleId, count] of Object.entries(scaleCounts)) {
    if (count > 1) add("duplicate_scale_id", "error", `Duplicate scale id: ${scaleId}`, `/scales/${scaleId}`);
  }

  const scaleIds = new Set(Object.keys(scaleCounts));
  spec.scales.forEach((scale, index) => {
    if (scale.max_score <= scale.min_score) {
      add("invalid_scale_range", "error", "Scale max_score must be greater than min_score", `/scales/${index}`);
    }
    if (scale.kind === "binary" && (scale.min_score !== 0 || scale.max_score !== 1)) {
      add("invalid_binary_scale", "error", "Binary scales must normalize to 0..1", `/scales/${index}`);
    }
  });

  let hasScope = Boolean(hasScopeBoundary(spec.scope) || spec.corpus_profile);
  spec.criteria.forEach((criterion, index) => {
    if (!scaleIds.has(criterion.scale_id)) {
      add("unknown_scale", "error", `Criterion ${criterion.id} references unknown scale`, `/criteria/${index}/scale_id`);
    }
    if (criterion.weight <= 0) add("invalid_weight", "error", "Criterion weight must be positive", `/criteria/${index}/weight`);
    if (hasScopeBoundary(criterion.scope)) hasScope = true;
    const genericCount = criterion.description
      .toLowerCase()
      .split(/\W+/)
      .filter((word) => genericWords.has(word)).length;
    if (genericCount > 2) {
      add("vague_criterion", "warning", "Criterion uses repeated generic terms", `/criteria/${index}/description`);
    }
  });

  if (!hasScope) add("missing_scope_boundaries", "warning", "Rubric has no explicit scope boundaries", "/scope");
  if (hypothesisLoaded.some((phrase) => spec.goal.toLowerCase().includes(phrase))) {
    add("hypothesis_loaded_goal", "warning", "Goal appears to load the desired conclusion", "/goal");
  }
  spec.output_constraints.forEach((constraint, index) => {
    if (constraint.toLowerCase().includes("xml")) {
      add(
        "unsupported_output_constraint",
        "warning",
        "XML-only output is not supported by the judge parser",
        `/output_constraints/${index}`,
      );
    }
  });

  const disqualifierCounts = countBy(spec.disqualifiers.map((disqualifier) => disqualifier.id));
  for (const [disqualifierId, count] of Object.entries(disqualifierCounts)) {
    if (count > 1) add("duplicate_disqualifier_id", "error", `Duplicate disqualifier id: ${disqualifierId}`);
  }

  return [...findings.values()].sort((a, b) => a.code.localeCompare(b.code));
}

export function compileRubricSpec(input: RubricSpec | string | Record<string, unknown>): CompiledRubric {
  const spec = coerceSpec(input);
  const findings = lintRubricSpec(spec);
  const errors = findings.filter((finding) => finding.severity === "error").map((finding) => finding.code).sort();
  if (errors.length) throw new Error(`invalid rubric: ${errors.join(", ")}`);

  const totalWeight = spec.criteria.reduce((total, criterion) => total + criterion.weight, 0) || 1;
  const normalizedWeights = Object.fromEntries(
    spec.criteria.map((criterion) => [criterion.id, round6(criterion.weight / totalWeight)]),
  );
  const criterionIds = spec.criteria.map((criterion) => criterion.id);
  const scaleIds = [...new Set(spec.scales.map((scale) => scale.id))];
  return new CompiledRubric(
    1,
    spec.rubric_id,
    criterionIds,
    criterionIds,
    scaleIds,
    normalizedWeights,
    findings,
    renderRubricPrompt(spec),
  );
}

export function renderRubricPrompt(input: RubricSpec | CompiledRubric | string | Record<string, unknown>): string {
  if (input instanceof CompiledRubric) return input.prompt_contract;
  const spec = coerceSpec(input);
  const lines = [`RubricSpec ${spec.rubric_id}`, `Goal: ${spec.goal}`, "Criteria:"];
  for (const criterion of spec.criteria) {
    lines.push(`- ${criterion.id} (weight ${criterion.weight}, scale ${criterion.scale_id}): ${criterion.description}`);
  }
  if (hasScopeBoundary(spec.scope)) {
    lines.push(`Scope include: ${spec.scope?.include.join(", ") || "n/a"}`);
    lines.push(`Scope exclude: ${spec.scope?.exclude.join(", ") || "n/a"}`);
  }
  if (spec.corpus_profile) {
    lines.push(`Corpus domain: ${spec.corpus_profile.domain}`);
    lines.push(`Corpus source: ${spec.corpus_profile.source_summary}`);
  }
  if (spec.disqualifiers.length) {
    lines.push("Disqualifiers:");
    for (const disqualifier of spec.disqualifiers) lines.push(`- ${disqualifier.id}: ${disqualifier.description}`);
  }
  if (spec.evidence_requirements.length) lines.push(`Evidence requirements: ${spec.evidence_requirements.join("; ")}`);
  if (spec.output_constraints.length) lines.push(`Output constraints: ${spec.output_constraints.join("; ")}`);
  lines.push(`Result dimensions: ${spec.criteria.map((criterion) => criterion.id).join(", ")}`);
  return lines.join("\n");
}

export function proposeRubricPatches(
  specInput: RubricSpec,
  anchors: Array<Record<string, unknown>>,
  opts: { experimental?: boolean } = {},
): RubricPatchProposal {
  if (!opts.experimental) throw new Error("rubric patch proposals are experimental");

  const spec = RubricSpecSchema.parse(specInput);
  const knownIds = new Set(spec.criteria.map((criterion) => criterion.id));
  const grouped = new Map<string, Array<Record<string, unknown>>>();
  const humanScores: number[] = [];
  const judgeScores: number[] = [];

  for (const anchor of anchors) {
    const criterionId = String(anchor.criterion_id ?? "");
    if (criterionId) grouped.set(criterionId, [...(grouped.get(criterionId) ?? []), anchor]);
    if (typeof anchor.human_score === "number" && typeof anchor.judge_score === "number") {
      humanScores.push(anchor.human_score);
      judgeScores.push(anchor.judge_score);
    }
  }

  const patches: RubricPatch[] = [];
  for (const [criterionId, items] of grouped.entries()) {
    if (!knownIds.has(criterionId) || items.length < 2) continue;
    const meanGap = average(items.map((item) => Math.abs(Number(item.judge_score ?? 0) - Number(item.human_score ?? 0))));
    if (meanGap < 0.2) continue;
    patches.push({
      op: "append",
      path: `/criteria/${criterionId}/description`,
      value: ` Calibration note: ${firstNote(items)}`,
      reason: `human anchors disagree with judge by ${meanGap.toFixed(3)}`,
    });
  }

  const meanError = average(humanScores.map((score, index) => Math.abs((judgeScores[index] ?? 0) - score)));
  return {
    patches,
    requires_human_review: true,
    metrics: {
      agreement: round6(1 - meanError),
      consistency: round6(1 - scoreRange(judgeScores)),
      discrimination: round6(scoreRange(humanScores)),
    },
  };
}

function coerceSpec(input: RubricSpec | string | Record<string, unknown>): RubricSpec {
  if (typeof input === "string") return legacyRubricSpec(input);
  return RubricSpecSchema.parse(input);
}

function countBy(values: string[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const value of values) counts[value] = (counts[value] ?? 0) + 1;
  return counts;
}

function hasScopeBoundary(scope?: RubricScope): boolean {
  return Boolean(scope && (scope.include.length > 0 || scope.exclude.length > 0));
}

function round6(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000;
}

function average(values: number[]): number {
  return values.length ? values.reduce((total, value) => total + value, 0) / values.length : 0;
}

function scoreRange(values: number[]): number {
  return values.length ? Math.max(...values) - Math.min(...values) : 0;
}

function firstNote(items: Array<Record<string, unknown>>): string {
  for (const item of items) {
    const note = String(item.human_notes ?? "").trim();
    if (note) return note;
  }
  return "tighten this criterion against human anchors";
}
