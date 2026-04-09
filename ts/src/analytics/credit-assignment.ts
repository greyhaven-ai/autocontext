/**
 * Component sensitivity profiling and credit assignment.
 *
 * TS port of autocontext.analytics.credit_assignment (AC-381).
 */

import { roundToDecimals } from "./number-utils.js";

function textChangeMagnitude(oldValue: string, newValue: string): number {
  if (oldValue === newValue) {
    return 0;
  }
  if (!oldValue && !newValue) {
    return 0;
  }
  if (!oldValue || !newValue) {
    return 1;
  }

  const maxLen = Math.max(oldValue.length, newValue.length);
  let common = 0;
  const overlap = Math.min(oldValue.length, newValue.length);
  for (let index = 0; index < overlap; index += 1) {
    if (oldValue[index] === newValue[index]) {
      common += 1;
    }
  }
  return roundToDecimals(1 - common / maxLen, 4);
}

function listChangeMagnitude(oldValues: unknown[], newValues: unknown[]): number {
  const oldSet = new Set(oldValues.map((value) => String(value)));
  const newSet = new Set(newValues.map((value) => String(value)));
  if (oldSet.size === newSet.size && [...oldSet].every((value) => newSet.has(value))) {
    return 0;
  }

  const union = new Set([...oldSet, ...newSet]);
  if (union.size === 0) {
    return 0;
  }

  let diff = 0;
  for (const value of union) {
    if (oldSet.has(value) !== newSet.has(value)) {
      diff += 1;
    }
  }
  return roundToDecimals(diff / union.size, 4);
}

export class ComponentChange {
  readonly component: string;
  readonly magnitude: number;
  readonly description: string;
  readonly metadata: Record<string, unknown>;

  constructor(
    component: string,
    magnitude: number,
    description: string,
    metadata: Record<string, unknown> = {},
  ) {
    this.component = component;
    this.magnitude = magnitude;
    this.description = description;
    this.metadata = metadata;
  }

  toDict(): Record<string, unknown> {
    return {
      component: this.component,
      magnitude: this.magnitude,
      description: this.description,
      metadata: this.metadata,
    };
  }

  static fromDict(data: Record<string, unknown>): ComponentChange {
    return new ComponentChange(
      String(data.component ?? ""),
      Number(data.magnitude ?? 0),
      String(data.description ?? ""),
      (data.metadata as Record<string, unknown>) ?? {},
    );
  }
}

export class GenerationChangeVector {
  readonly generation: number;
  readonly scoreDelta: number;
  readonly changes: ComponentChange[];
  readonly metadata: Record<string, unknown>;

  constructor(
    generation: number,
    scoreDelta: number,
    changes: ComponentChange[],
    metadata: Record<string, unknown> = {},
  ) {
    this.generation = generation;
    this.scoreDelta = scoreDelta;
    this.changes = changes;
    this.metadata = metadata;
  }

  get totalChangeMagnitude(): number {
    return roundToDecimals(this.changes.reduce((sum, change) => sum + change.magnitude, 0), 6);
  }

  toDict(): Record<string, unknown> {
    return {
      generation: this.generation,
      score_delta: this.scoreDelta,
      changes: this.changes.map((change) => change.toDict()),
      metadata: this.metadata,
    };
  }

  static fromDict(data: Record<string, unknown>): GenerationChangeVector {
    const rawChanges = Array.isArray(data.changes) ? data.changes : [];
    return new GenerationChangeVector(
      Number(data.generation ?? 0),
      Number(data.score_delta ?? 0),
      rawChanges.map((change) => ComponentChange.fromDict(change as Record<string, unknown>)),
      (data.metadata as Record<string, unknown>) ?? {},
    );
  }
}

export class AttributionResult {
  readonly generation: number;
  readonly totalDelta: number;
  readonly credits: Record<string, number>;
  readonly metadata: Record<string, unknown>;

  constructor(
    generation: number,
    totalDelta: number,
    credits: Record<string, number>,
    metadata: Record<string, unknown> = {},
  ) {
    this.generation = generation;
    this.totalDelta = totalDelta;
    this.credits = credits;
    this.metadata = metadata;
  }

  toDict(): Record<string, unknown> {
    return {
      generation: this.generation,
      total_delta: this.totalDelta,
      credits: this.credits,
      metadata: this.metadata,
    };
  }

  static fromDict(data: Record<string, unknown>): AttributionResult {
    const rawCredits = data.credits;
    const credits: Record<string, number> = {};
    if (rawCredits && typeof rawCredits === "object" && !Array.isArray(rawCredits)) {
      for (const [component, value] of Object.entries(rawCredits)) {
        credits[String(component)] = Number(value);
      }
    }
    return new AttributionResult(
      Number(data.generation ?? 0),
      Number(data.total_delta ?? 0),
      credits,
      (data.metadata as Record<string, unknown>) ?? {},
    );
  }
}

export class CreditAssignmentRecord {
  readonly runId: string;
  readonly generation: number;
  readonly vector: GenerationChangeVector;
  readonly attribution: AttributionResult;
  readonly metadata: Record<string, unknown>;

  constructor(
    runId: string,
    generation: number,
    vector: GenerationChangeVector,
    attribution: AttributionResult,
    metadata: Record<string, unknown> = {},
  ) {
    this.runId = runId;
    this.generation = generation;
    this.vector = vector;
    this.attribution = attribution;
    this.metadata = metadata;
  }

  toDict(): Record<string, unknown> {
    return {
      run_id: this.runId,
      generation: this.generation,
      vector: this.vector.toDict(),
      attribution: this.attribution.toDict(),
      metadata: this.metadata,
    };
  }

  static fromDict(data: Record<string, unknown>): CreditAssignmentRecord {
    return new CreditAssignmentRecord(
      String(data.run_id ?? ""),
      Number(data.generation ?? 0),
      GenerationChangeVector.fromDict((data.vector as Record<string, unknown>) ?? {}),
      AttributionResult.fromDict((data.attribution as Record<string, unknown>) ?? {}),
      (data.metadata as Record<string, unknown>) ?? {},
    );
  }
}

export function computeChangeVector(
  generation: number,
  scoreDelta: number,
  previousState: Record<string, unknown>,
  currentState: Record<string, unknown>,
): GenerationChangeVector {
  const changes: ComponentChange[] = [];

  const oldPlaybook = String(previousState.playbook ?? "");
  const newPlaybook = String(currentState.playbook ?? "");
  const playbookMagnitude = textChangeMagnitude(oldPlaybook, newPlaybook);
  if (playbookMagnitude > 0) {
    changes.push(new ComponentChange("playbook", playbookMagnitude, `Playbook changed (${Math.round(playbookMagnitude * 100)}%)`));
  }

  const oldTools = Array.isArray(previousState.tools) ? previousState.tools : [];
  const newTools = Array.isArray(currentState.tools) ? currentState.tools : [];
  const toolsMagnitude = listChangeMagnitude(oldTools, newTools);
  if (toolsMagnitude > 0) {
    const oldSet = new Set(oldTools.map((value) => String(value)));
    const newSet = new Set(newTools.map((value) => String(value)));
    const added = [...newSet].filter((value) => !oldSet.has(value)).length;
    const removed = [...oldSet].filter((value) => !newSet.has(value)).length;
    changes.push(new ComponentChange("tools", toolsMagnitude, `+${added}/-${removed} tools`));
  }

  const oldHints = String(previousState.hints ?? "");
  const newHints = String(currentState.hints ?? "");
  const hintsMagnitude = textChangeMagnitude(oldHints, newHints);
  if (hintsMagnitude > 0) {
    changes.push(new ComponentChange("hints", hintsMagnitude, `Hints changed (${Math.round(hintsMagnitude * 100)}%)`));
  }

  const oldAnalysis = String(previousState.analysis ?? "");
  const newAnalysis = String(currentState.analysis ?? "");
  const analysisMagnitude = textChangeMagnitude(oldAnalysis, newAnalysis);
  if (analysisMagnitude > 0) {
    changes.push(new ComponentChange("analysis", analysisMagnitude, `Analysis changed (${Math.round(analysisMagnitude * 100)}%)`));
  }

  return new GenerationChangeVector(generation, scoreDelta, changes);
}

export function attributeCredit(vector: GenerationChangeVector): AttributionResult {
  if (vector.scoreDelta <= 0 || vector.changes.length === 0) {
    const zeroCredits = Object.fromEntries(vector.changes.map((change) => [change.component, 0]));
    return new AttributionResult(vector.generation, vector.scoreDelta, zeroCredits);
  }

  const totalMagnitude = vector.totalChangeMagnitude;
  if (totalMagnitude === 0) {
    const zeroCredits = Object.fromEntries(vector.changes.map((change) => [change.component, 0]));
    return new AttributionResult(vector.generation, vector.scoreDelta, zeroCredits);
  }

  const credits: Record<string, number> = {};
  for (const change of vector.changes) {
    credits[change.component] = roundToDecimals(vector.scoreDelta * (change.magnitude / totalMagnitude), 6);
  }
  return new AttributionResult(vector.generation, vector.scoreDelta, credits);
}

const ROLE_COMPONENT_PRIORITY: Record<string, string[]> = {
  analyst: ["analysis", "playbook", "hints"],
  coach: ["playbook", "hints", "analysis"],
  architect: ["tools"],
  competitor: ["playbook", "hints"],
};

const ROLE_TITLES: Record<string, string> = {
  analyst: "Previous Analysis Attribution",
  coach: "Previous Coaching Attribution",
  architect: "Previous Tooling Attribution",
  competitor: "Previous Strategy Attribution",
};

const ROLE_GUIDANCE: Record<string, string> = {
  analyst: "Use this to focus your next diagnosis on the changes that actually moved score.",
  coach: "Use this to reinforce the coaching changes that translated into measurable gains.",
  architect: "Use this to prioritize tool work only where tooling actually moved outcomes.",
  competitor: "Use this to lean into the strategy surfaces that correlated with progress.",
};

export function formatAttributionForAgent(result: AttributionResult, role: string): string {
  if (Object.keys(result.credits).length === 0 || result.totalDelta <= 0) {
    return "";
  }

  const normalizedRole = role.trim().toLowerCase();
  const title = ROLE_TITLES[normalizedRole] ?? "Credit Attribution";
  const guidance = ROLE_GUIDANCE[normalizedRole] ?? "";
  const preferred = ROLE_COMPONENT_PRIORITY[normalizedRole] ?? [];
  const orderedComponents: string[] = [];

  for (const component of preferred) {
    if (component in result.credits) {
      orderedComponents.push(component);
    }
  }

  const remaining = Object.entries(result.credits)
    .sort((left, right) => {
      if (right[1] !== left[1]) {
        return right[1] - left[1];
      }
      return left[0].localeCompare(right[0]);
    })
    .map(([component]) => component);
  for (const component of remaining) {
    if (!orderedComponents.includes(component)) {
      orderedComponents.push(component);
    }
  }

  const lines = [`## ${title} (Gen ${result.generation})`, `Total score improvement: +${result.totalDelta.toFixed(4)}`];
  if (guidance) {
    lines.push(guidance);
  }
  lines.push("");

  for (const component of orderedComponents) {
    const credit = result.credits[component] ?? 0;
    const share = result.totalDelta > 0 ? (credit / result.totalDelta) * 100 : 0;
    lines.push(`- ${component}: +${credit.toFixed(4)} (${Math.round(share)}% of improvement)`);
  }

  return lines.join("\n");
}

export function summarizeCreditPatterns(records: CreditAssignmentRecord[]): Record<string, unknown> {
  const componentRollup = new Map<string, Record<string, unknown>>();
  const runIds = [...new Set(records.map((record) => record.runId).filter(Boolean))].sort();

  for (const record of records) {
    const totalDelta = Math.max(record.attribution.totalDelta, 0);
    for (const change of record.vector.changes) {
      const bucket = componentRollup.get(change.component) ?? {
        component: change.component,
        generationCount: 0,
        positiveGenerationCount: 0,
        totalCredit: 0,
        totalChangeMagnitude: 0,
        averageCredit: 0,
        averageShare: 0,
      };

      bucket.generationCount = Number(bucket.generationCount) + 1;
      bucket.totalChangeMagnitude = roundToDecimals(Number(bucket.totalChangeMagnitude) + change.magnitude, 6);

      const credit = Number(record.attribution.credits[change.component] ?? 0);
      if (credit > 0) {
        bucket.positiveGenerationCount = Number(bucket.positiveGenerationCount) + 1;
      }
      bucket.totalCredit = roundToDecimals(Number(bucket.totalCredit) + credit, 6);

      if (totalDelta > 0) {
        bucket.averageShare = roundToDecimals(Number(bucket.averageShare) + credit / totalDelta, 6);
      }
      componentRollup.set(change.component, bucket);
    }
  }

  const components = [...componentRollup.values()].map((bucket) => {
    const generationCount = Number(bucket.generationCount);
    if (generationCount > 0) {
      bucket.averageCredit = roundToDecimals(Number(bucket.totalCredit) / generationCount, 6);
      bucket.averageShare = roundToDecimals(Number(bucket.averageShare) / generationCount, 6);
    }
    return { ...bucket };
  });

  components.sort((left, right) => {
    const creditDelta = Number(right.totalCredit) - Number(left.totalCredit);
    if (creditDelta !== 0) {
      return creditDelta;
    }
    return String(left.component).localeCompare(String(right.component));
  });

  return {
    totalRecords: records.length,
    runCount: runIds.length,
    runIds,
    components,
  };
}

export class CreditAssigner {
  #contributions: Map<string, number[]> = new Map();

  recordContribution(component: string, scoreDelta: number): void {
    const existing = this.#contributions.get(component) ?? [];
    existing.push(scoreDelta);
    this.#contributions.set(component, existing);
  }

  getCredits(): Record<string, number> {
    const credits: Record<string, number> = {};
    for (const [component, deltas] of this.#contributions) {
      credits[component] = deltas.reduce((sum, delta) => sum + delta, 0);
    }
    return credits;
  }

  computeChangeVector(
    generation: number,
    scoreDelta: number,
    previousState: Record<string, unknown>,
    currentState: Record<string, unknown>,
  ): GenerationChangeVector {
    return computeChangeVector(generation, scoreDelta, previousState, currentState);
  }

  attributeCredit(vector: GenerationChangeVector): AttributionResult {
    const attribution = attributeCredit(vector);
    for (const [component, credit] of Object.entries(attribution.credits)) {
      this.recordContribution(component, credit);
    }
    return attribution;
  }

  formatAttributionForAgent(result: AttributionResult, role: string): string {
    return formatAttributionForAgent(result, role);
  }

  summarizeCreditPatterns(records: CreditAssignmentRecord[]): Record<string, unknown> {
    return summarizeCreditPatterns(records);
  }
}
