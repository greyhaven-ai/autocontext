export interface TokenPressureObservation {
  position: number;
  studentLogprob: number;
  teacherLogprob: number;
  studentEntropy?: number;
  tokenText?: string;
}

export interface TokenPressurePositionSummary {
  position: number;
  count: number;
  positivePressureRatio: number;
  negativePressureRatio: number;
  meanMargin: number;
  meanStudentEntropy: number | null;
}

export interface TokenPressureSpike {
  position: number;
  margin: number;
  direction: "positive" | "negative";
  tokenText?: string;
}

export interface TokenPressureReport {
  schemaVersion: 1;
  runId: string;
  backend: string;
  mode: string;
  seed: number;
  tokenCount: number;
  positivePressureRatio: number;
  negativePressureRatio: number;
  neutralPressureRatio: number;
  meanPositiveMargin: number | null;
  meanNegativeMargin: number | null;
  meanMargin: number | null;
  meanStudentEntropy: number | null;
  meanResponseLength: number | null;
  positionPressure: TokenPressurePositionSummary[];
  shockThreshold: number;
  shockSpikeCount: number;
  shockSpikes: TokenPressureSpike[];
  rawTokenTextPersisted: boolean;
}

export function buildTokenPressureReport(
  observations: TokenPressureObservation[],
  opts: {
    backend: string;
    mode: string;
    seed?: number;
    runId?: string;
    responseLengths?: number[];
    shockThreshold?: number;
    includeTokenText?: boolean;
  },
): TokenPressureReport {
  const margins = observations.map((obs) => obs.teacherLogprob - obs.studentLogprob);
  const positive = margins.filter((margin) => margin > 0);
  const negative = margins.filter((margin) => margin < 0);
  const shockThreshold = opts.shockThreshold ?? 2;
  const includeTokenText = opts.includeTokenText ?? false;
  const shocks = observations.filter(
    (obs) => Math.abs(obs.teacherLogprob - obs.studentLogprob) >= shockThreshold,
  );
  return {
    schemaVersion: 1,
    runId: opts.runId ?? "",
    backend: opts.backend,
    mode: opts.mode,
    seed: opts.seed ?? 0,
    tokenCount: observations.length,
    positivePressureRatio: ratio(positive.length, observations.length),
    negativePressureRatio: ratio(negative.length, observations.length),
    neutralPressureRatio: ratio(
      observations.length - positive.length - negative.length,
      observations.length,
    ),
    meanPositiveMargin: mean(positive),
    meanNegativeMargin: mean(negative),
    meanMargin: mean(margins),
    meanStudentEntropy: mean(observations.map((obs) => obs.studentEntropy)),
    meanResponseLength: mean(opts.responseLengths ?? []),
    positionPressure: positionPressure(observations),
    shockThreshold,
    shockSpikeCount: shocks.length,
    shockSpikes: shocks.map((obs) => shock(obs, includeTokenText)),
    rawTokenTextPersisted: includeTokenText,
  };
}

export function compareTokenPressureReports(
  reports: Array<
    Pick<
      TokenPressureReport,
      "runId" | "positivePressureRatio" | "negativePressureRatio" | "shockSpikeCount"
    >
  >,
): {
  schemaVersion: 1;
  runCount: number;
  meanPositivePressureRatio: number | null;
  meanNegativePressureRatio: number | null;
  highestPositivePressureRunId: string | null;
  highestShockRunId: string | null;
  runs: Array<
    Pick<
      TokenPressureReport,
      "runId" | "positivePressureRatio" | "negativePressureRatio" | "shockSpikeCount"
    >
  >;
} {
  return {
    schemaVersion: 1,
    runCount: reports.length,
    meanPositivePressureRatio: mean(reports.map((report) => report.positivePressureRatio)),
    meanNegativePressureRatio: mean(reports.map((report) => report.negativePressureRatio)),
    highestPositivePressureRunId: maxRun(reports, "positivePressureRatio"),
    highestShockRunId: maxRun(reports, "shockSpikeCount"),
    runs: reports,
  };
}

function positionPressure(
  observations: TokenPressureObservation[],
): TokenPressurePositionSummary[] {
  const byPosition = new Map<number, TokenPressureObservation[]>();
  for (const obs of observations) {
    byPosition.set(obs.position, [...(byPosition.get(obs.position) ?? []), obs]);
  }
  return [...byPosition.entries()]
    .sort(([left], [right]) => left - right)
    .map(([position, items]) => {
      const margins = items.map((obs) => obs.teacherLogprob - obs.studentLogprob);
      return {
        position,
        count: items.length,
        positivePressureRatio: ratio(margins.filter((margin) => margin > 0).length, items.length),
        negativePressureRatio: ratio(margins.filter((margin) => margin < 0).length, items.length),
        meanMargin: mean(margins) ?? 0,
        meanStudentEntropy: mean(items.map((obs) => obs.studentEntropy)),
      };
    });
}

function shock(obs: TokenPressureObservation, includeTokenText: boolean): TokenPressureSpike {
  const margin = obs.teacherLogprob - obs.studentLogprob;
  return {
    position: obs.position,
    margin,
    direction: margin > 0 ? "positive" : "negative",
    ...(includeTokenText ? { tokenText: obs.tokenText ?? "" } : {}),
  };
}

function ratio(count: number, total: number): number {
  return total === 0 ? 0 : count / total;
}

function mean(values: Array<number | undefined>): number | null {
  const numeric = values.filter(
    (value): value is number => typeof value === "number" && Number.isFinite(value),
  );
  return numeric.length === 0
    ? null
    : numeric.reduce((sum, value) => sum + value, 0) / numeric.length;
}

function maxRun(reports: Array<Record<string, unknown>>, key: string): string | null {
  if (reports.length === 0) return null;
  const winner = reports.reduce((best, report) =>
    Number(report[key] ?? 0) > Number(best[key] ?? 0) ? report : best,
  );
  return String(winner.runId ?? "");
}
