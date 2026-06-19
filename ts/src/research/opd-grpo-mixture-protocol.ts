export const MIXTURE_ARMS = [
  "grpo",
  "full_opd",
  "positive_opd",
  "mixed_positive_opd_grpo",
] as const;
export type MixtureArm = (typeof MIXTURE_ARMS)[number];

export const REQUIRED_MIXTURE_METRICS = [
  "final_score",
  "heldout_score",
  "response_length",
  "diversity",
  "entropy",
  "kl",
  "token_pressure",
  "cost_time",
] as const;

export interface MixtureRunSpec {
  arm: MixtureArm;
  scenario: string;
  seed: number;
  maxSteps: number;
  nPrompts: number;
  trlMode: "gkd" | "grpo";
  positivePressure: boolean;
  trainingMixture: string;
  command: string;
  requiredMetrics: readonly string[];
}

export interface MixtureExperimentMatrix {
  schemaVersion: 1;
  scenario: string;
  matchedCompute: { nPrompts: number; steps: number[]; arms: MixtureArm[] };
  seedNotes: string;
  requiredMetrics: readonly string[];
  promotionPolicy: string;
  runs: MixtureRunSpec[];
}

export interface MixtureResultRow {
  arm: string;
  seed?: number;
  finalScore?: number;
  heldoutScore?: number;
  responseLength?: number;
  diversity?: number;
  entropy?: number;
  kl?: number;
  tokenPressure?: number;
  costTime?: number;
  collapseDetected?: boolean;
}

export function buildExperimentMatrix(opts: {
  scenario: string;
  seeds?: number[];
  steps?: number[];
  prompts?: number;
  studentModel?: string;
  teacherModel?: string;
}): MixtureExperimentMatrix {
  const seeds = opts.seeds ?? [0, 1, 2];
  const steps = opts.steps ?? [1000, 2000];
  const prompts = opts.prompts ?? 384;
  const runs = steps.flatMap((maxSteps) =>
    seeds.flatMap((seed) =>
      MIXTURE_ARMS.map((arm) =>
        runSpec(arm, {
          scenario: opts.scenario,
          seed,
          maxSteps,
          prompts,
          studentModel: opts.studentModel ?? "Qwen/Qwen2.5-1.5B-Instruct",
          teacherModel: opts.teacherModel ?? "Qwen/Qwen2.5-3B-Instruct",
        }),
      ),
    ),
  );
  return {
    schemaVersion: 1,
    scenario: opts.scenario,
    matchedCompute: { nPrompts: prompts, steps, arms: [...MIXTURE_ARMS] },
    seedNotes: `${seeds.length} seeds: ${seeds.join(", ")}`,
    requiredMetrics: REQUIRED_MIXTURE_METRICS,
    promotionPolicy: "Do not promote mixed mode unless held-out score improves without collapse.",
    runs,
  };
}

function runSpec(
  arm: MixtureArm,
  opts: {
    scenario: string;
    seed: number;
    maxSteps: number;
    prompts: number;
    studentModel: string;
    teacherModel: string;
  },
): MixtureRunSpec {
  const trlMode = arm === "grpo" ? "grpo" : "gkd";
  const positivePressure = arm === "positive_opd" || arm === "mixed_positive_opd_grpo";
  const trainingMixture = arm === "mixed_positive_opd_grpo" ? "positive_opd=0.5,grpo=0.5" : "";
  let command = `python -m autocontext.training.autoresearch.trl_backend --mode ${trlMode} --scenario ${opts.scenario} --student-model ${opts.studentModel} --teacher-model ${opts.teacherModel} --n-prompts ${opts.prompts} --max-steps ${opts.maxSteps} --seed ${opts.seed}`;
  if (positivePressure) command += " --positive-pressure";
  if (trainingMixture) command += ` --training-mixture ${trainingMixture}`;
  return {
    arm,
    scenario: opts.scenario,
    seed: opts.seed,
    maxSteps: opts.maxSteps,
    nPrompts: opts.prompts,
    trlMode,
    positivePressure,
    trainingMixture,
    command,
    requiredMetrics: REQUIRED_MIXTURE_METRICS,
  };
}

export function summarizeMixtureResults(
  rows: MixtureResultRow[],
  minHeldoutDelta = 0.01,
): {
  schemaVersion: 1;
  arms: Record<string, Record<string, number | boolean | null>>;
  promotion: { promoteMixed: boolean; reason: string };
} {
  const grouped = new Map<string, MixtureResultRow[]>();
  for (const row of rows) grouped.set(row.arm, [...(grouped.get(row.arm) ?? []), row]);
  const arms = Object.fromEntries(
    [...grouped.entries()].sort().map(([arm, items]) => [arm, summarizeArm(items)]),
  );
  return { schemaVersion: 1, arms, promotion: promotionDecision(arms, minHeldoutDelta) };
}

function summarizeArm(rows: MixtureResultRow[]): Record<string, number | boolean | null> {
  return {
    seedCount: new Set(rows.map((row) => row.seed)).size,
    meanFinalScore: mean(rows.map((row) => row.finalScore)),
    meanHeldoutScore: mean(rows.map((row) => row.heldoutScore)),
    meanResponseLength: mean(rows.map((row) => row.responseLength)),
    meanDiversity: mean(rows.map((row) => row.diversity)),
    meanEntropy: mean(rows.map((row) => row.entropy)),
    meanKl: mean(rows.map((row) => row.kl)),
    meanTokenPressure: mean(rows.map((row) => row.tokenPressure)),
    meanCostTime: mean(rows.map((row) => row.costTime)),
    collapseDetected: rows.some(collapse),
  };
}

function promotionDecision(
  arms: Record<string, Record<string, number | boolean | null>>,
  minHeldoutDelta: number,
): { promoteMixed: boolean; reason: string } {
  const mixed = arms.mixed_positive_opd_grpo;
  const baselines = Object.entries(arms)
    .filter(([arm]) => arm !== "mixed_positive_opd_grpo")
    .map(([, summary]) => summary);
  if (!mixed || baselines.length === 0)
    return { promoteMixed: false, reason: "missing_comparison" };
  if (mixed.collapseDetected) return { promoteMixed: false, reason: "collapse_detected" };
  const mixedScore = Number(mixed.meanHeldoutScore ?? 0);
  const bestBaseline = Math.max(
    ...baselines.map((summary) => Number(summary.meanHeldoutScore ?? 0)),
  );
  if (mixedScore >= bestBaseline + minHeldoutDelta) {
    return { promoteMixed: true, reason: "heldout_improved_without_collapse" };
  }
  return { promoteMixed: false, reason: "heldout_not_improved" };
}

export function renderProtocolReport(matrix: MixtureExperimentMatrix): string {
  return [
    "# OPD/GKD + GRPO mixture experiment protocol",
    "",
    "Matched-compute arms: GRPO, full OPD/GKD, positive-pressure OPD, and mixed positive-pressure OPD + GRPO.",
    "Compare against AC-787/AC-789 methodology where applicable.",
    "",
    "Required result fields:",
    ...matrix.requiredMetrics.map((metric) => `- ${metric}`),
    "",
    `Promotion rule: ${matrix.promotionPolicy}`,
    "",
    "Run commands:",
    ...matrix.runs.map((run) => `- \`${run.command}\``),
  ].join("\n");
}

function mean(values: Array<number | undefined>): number | null {
  const numeric = values.filter(
    (value): value is number => typeof value === "number" && Number.isFinite(value),
  );
  return numeric.length === 0
    ? null
    : Math.round((numeric.reduce((sum, value) => sum + value, 0) / numeric.length) * 1_000_000) /
        1_000_000;
}

function collapse(row: MixtureResultRow): boolean {
  return (
    row.collapseDetected === true ||
    (row.entropy ?? Infinity) < 0.5 ||
    (row.diversity ?? Infinity) < 0.05
  );
}
