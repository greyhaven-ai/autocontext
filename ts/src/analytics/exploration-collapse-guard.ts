export type GuidanceKind =
  | "hint"
  | "playbook_update"
  | "teacher_signal"
  | "pressure_mode"
  | "other";
export type CollapseMetric =
  | "response_length"
  | "diversity"
  | "entropy"
  | "route_repetition"
  | "rollback_rate"
  | "score";
export type MitigationAction = "none" | "demote_guidance";

export type ExplorationSnapshot = {
  generationIndex: number;
  responseLength: number;
  diversity?: number;
  entropy?: number;
  routeSignature?: string;
  rollbackRate?: number;
  score?: number;
};

export type GuidanceChange = {
  changeId: string;
  generationIndex: number;
  kind: GuidanceKind;
  sourceComponent: string;
  sourceSpan?: string;
};

export type ExplorationCollapseThresholds = {
  window: number;
  minSignals: number;
  responseLengthDropRatio: number;
  diversityDropRatio: number;
  entropyDropRatio: number;
  routeRepetitionIncrease: number;
  rollbackRateIncrease: number;
  scoreDrop: number;
};

export type ExplorationCollapseSignal = {
  metric: CollapseMetric;
  before: number;
  after: number;
  delta: number;
  threshold: number;
};

export type ExplorationCollapseEvent = {
  eventType: "exploration_collapse_detected";
  guidanceChange: GuidanceChange;
  advisoryOnly: boolean;
  mitigation: MitigationAction;
  signals: ExplorationCollapseSignal[];
  recommendation: string;
};

export type ExplorationCollapseRecord = {
  event_type: "exploration_collapse_detected";
  payload: {
    guidance_change: {
      change_id: string;
      generation_index: number;
      kind: GuidanceKind;
      source_component: string;
      source_span?: string;
    };
    advisory_only: boolean;
    mitigation: MitigationAction;
    signals: Array<{
      metric: CollapseMetric;
      before: number;
      after: number;
      delta: number;
      threshold: number;
    }>;
    recommendation: string;
  };
};

export type ExplorationCollapseReport = {
  schemaVersion: 1;
  advisoryOnly: boolean;
  events: ExplorationCollapseEvent[];
  records: ExplorationCollapseRecord[];
};

export type ExplorationCollapseOptions = {
  advisoryOnly?: boolean;
  autoMitigation?: boolean;
  thresholds?: Partial<ExplorationCollapseThresholds>;
};

const DEFAULT_THRESHOLDS: ExplorationCollapseThresholds = {
  window: 2,
  minSignals: 2,
  responseLengthDropRatio: 0.25,
  diversityDropRatio: 0.25,
  entropyDropRatio: 0.25,
  routeRepetitionIncrease: 0.3,
  rollbackRateIncrease: 0.2,
  scoreDrop: 0.05,
};

export function detectExplorationCollapse(
  snapshots: ExplorationSnapshot[],
  guidanceChanges: GuidanceChange[],
  options: ExplorationCollapseOptions = {},
): ExplorationCollapseReport {
  const advisoryOnly = options.advisoryOnly ?? true;
  const thresholds = { ...DEFAULT_THRESHOLDS, ...options.thresholds };
  const ordered = [...snapshots].sort(
    (left, right) => left.generationIndex - right.generationIndex,
  );
  const events: ExplorationCollapseEvent[] = [];

  for (const change of guidanceChanges) {
    const before = ordered
      .filter((snapshot) => snapshot.generationIndex < change.generationIndex)
      .slice(-thresholds.window);
    const after = ordered
      .filter((snapshot) => snapshot.generationIndex >= change.generationIndex)
      .slice(0, thresholds.window);
    if (before.length === 0 || after.length === 0) continue;

    const signals = collapseSignals(before, after, thresholds);
    if (signals.length >= thresholds.minSignals) {
      events.push({
        eventType: "exploration_collapse_detected",
        guidanceChange: change,
        advisoryOnly,
        mitigation: options.autoMitigation && !advisoryOnly ? "demote_guidance" : "none",
        signals,
        recommendation: recommendation(Boolean(options.autoMitigation), advisoryOnly),
      });
    }
  }

  return {
    schemaVersion: 1,
    advisoryOnly,
    events,
    records: events.map(eventRecord),
  };
}

export function renderExplorationCollapseReport(report: ExplorationCollapseReport): string {
  if (report.events.length === 0) return "No exploration collapse detected.";
  const lines = ["# Exploration Collapse Guard", ""];
  for (const event of report.events) {
    const change = event.guidanceChange;
    const span = change.sourceSpan ? ` span=${change.sourceSpan}` : "";
    lines.push(
      `- ${change.changeId} (${change.kind}) at generation ${change.generationIndex}: ` +
        `source=${change.sourceComponent}${span}; ` +
        `metrics=${event.signals.map((signal) => signal.metric).join(", ")}; ` +
        `mitigation=${event.mitigation}.`,
    );
  }
  return `${lines.join("\n")}\n`;
}

function collapseSignals(
  before: ExplorationSnapshot[],
  after: ExplorationSnapshot[],
  thresholds: ExplorationCollapseThresholds,
): ExplorationCollapseSignal[] {
  const signals: ExplorationCollapseSignal[] = [];
  dropSignal(
    signals,
    "response_length",
    avg(before, (snapshot) => snapshot.responseLength),
    avg(after, (snapshot) => snapshot.responseLength),
    thresholds.responseLengthDropRatio,
  );
  dropSignal(
    signals,
    "diversity",
    avg(before, (snapshot) => snapshot.diversity),
    avg(after, (snapshot) => snapshot.diversity),
    thresholds.diversityDropRatio,
  );
  dropSignal(
    signals,
    "entropy",
    avg(before, (snapshot) => snapshot.entropy),
    avg(after, (snapshot) => snapshot.entropy),
    thresholds.entropyDropRatio,
  );
  riseSignal(
    signals,
    "route_repetition",
    routeRepetition(before),
    routeRepetition(after),
    thresholds.routeRepetitionIncrease,
  );
  riseSignal(
    signals,
    "rollback_rate",
    avg(before, (snapshot) => snapshot.rollbackRate),
    avg(after, (snapshot) => snapshot.rollbackRate),
    thresholds.rollbackRateIncrease,
  );
  absoluteDropSignal(
    signals,
    "score",
    avg(before, (snapshot) => snapshot.score),
    avg(after, (snapshot) => snapshot.score),
    thresholds.scoreDrop,
  );
  return signals;
}

function dropSignal(
  signals: ExplorationCollapseSignal[],
  metric: CollapseMetric,
  before: number | undefined,
  after: number | undefined,
  threshold: number,
): void {
  if (before === undefined || after === undefined || before <= 0) return;
  if ((before - after) / before >= threshold)
    appendSignal(signals, metric, before, after, threshold);
}

function absoluteDropSignal(
  signals: ExplorationCollapseSignal[],
  metric: CollapseMetric,
  before: number | undefined,
  after: number | undefined,
  threshold: number,
): void {
  if (before !== undefined && after !== undefined && before - after >= threshold) {
    appendSignal(signals, metric, before, after, threshold);
  }
}

function riseSignal(
  signals: ExplorationCollapseSignal[],
  metric: CollapseMetric,
  before: number | undefined,
  after: number | undefined,
  threshold: number,
): void {
  if (before !== undefined && after !== undefined && after - before >= threshold) {
    appendSignal(signals, metric, before, after, threshold);
  }
}

function appendSignal(
  signals: ExplorationCollapseSignal[],
  metric: CollapseMetric,
  before: number,
  after: number,
  threshold: number,
): void {
  signals.push({
    metric,
    before: round(before),
    after: round(after),
    delta: round(after - before),
    threshold,
  });
}

function avg(
  items: ExplorationSnapshot[],
  selector: (snapshot: ExplorationSnapshot) => number | undefined,
): number | undefined {
  const values = items
    .map(selector)
    .filter((value): value is number => typeof value === "number" && Number.isFinite(value));
  if (values.length === 0) return undefined;
  return round(values.reduce((total, value) => total + value, 0) / values.length);
}

function routeRepetition(items: ExplorationSnapshot[]): number | undefined {
  const routes = items
    .map((item) => item.routeSignature)
    .filter((route): route is string => Boolean(route));
  if (routes.length === 0) return undefined;
  const counts = new Map<string, number>();
  for (const route of routes) counts.set(route, (counts.get(route) ?? 0) + 1);
  return round(Math.max(...counts.values()) / routes.length);
}

function eventRecord(event: ExplorationCollapseEvent): ExplorationCollapseRecord {
  const change = event.guidanceChange;
  return {
    event_type: "exploration_collapse_detected",
    payload: {
      guidance_change: {
        change_id: change.changeId,
        generation_index: change.generationIndex,
        kind: change.kind,
        source_component: change.sourceComponent,
        ...(change.sourceSpan ? { source_span: change.sourceSpan } : {}),
      },
      advisory_only: event.advisoryOnly,
      mitigation: event.mitigation,
      signals: event.signals.map((signal) => ({ ...signal })),
      recommendation: event.recommendation,
    },
  };
}

function recommendation(autoMitigation: boolean, advisoryOnly: boolean): string {
  if (autoMitigation && !advisoryOnly) {
    return "Demote the associated guidance and switch to exploration-heavy sampling.";
  }
  return "Warn only; inspect the associated guidance before changing run behavior.";
}

function round(value: number): number {
  return Math.round(value * 1_000_000) / 1_000_000;
}
