import type {
  ActivationState,
  ModelRecord,
  PromotionEvent,
} from "./promotion-types.js";

export function generateModelId(): string {
  return `model_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

export function createModelRecord(opts: {
  artifactId?: string;
  scenario: string;
  family: string;
  backend: string;
  checkpointDir: string;
  activationState?: ActivationState;
}): ModelRecord {
  return {
    artifactId: opts.artifactId ?? generateModelId(),
    scenario: opts.scenario,
    family: opts.family,
    backend: opts.backend,
    checkpointDir: opts.checkpointDir,
    activationState: opts.activationState ?? "candidate",
    promotionHistory: [],
    registeredAt: new Date().toISOString(),
  };
}

export function buildPromotionEvent(opts: {
  from: ActivationState;
  to: ActivationState;
  reason: string;
  evidence?: Record<string, unknown>;
}): PromotionEvent {
  return {
    from: opts.from,
    to: opts.to,
    reason: opts.reason,
    evidence: opts.evidence,
    timestamp: new Date().toISOString(),
  };
}

export function listModelRecordsForScenario(
  records: Iterable<ModelRecord>,
  scenario: string,
): ModelRecord[] {
  return [...records].filter((record) => record.scenario === scenario);
}

export function resolveActiveModelRecord(
  records: Iterable<ModelRecord>,
  scenario: string,
): ModelRecord | null {
  return listModelRecordsForScenario(records, scenario).find(
    (record) => record.activationState === "active",
  ) ?? null;
}

export function applyModelStateTransition(opts: {
  records: Map<string, ModelRecord>;
  artifactId: string;
  targetState: ActivationState;
  reason?: string;
  evidence?: Record<string, unknown>;
}): void {
  const record = opts.records.get(opts.artifactId);
  if (!record) {
    return;
  }

  const fromState = record.activationState;
  if (opts.targetState === "active") {
    for (const candidate of opts.records.values()) {
      if (
        candidate.scenario === record.scenario
        && candidate.activationState === "active"
        && candidate.artifactId !== opts.artifactId
      ) {
        candidate.activationState = "disabled";
        candidate.promotionHistory.push(buildPromotionEvent({
          from: "active",
          to: "disabled",
          reason: `Displaced by ${opts.artifactId}`,
        }));
      }
    }
  }

  record.activationState = opts.targetState;
  record.promotionHistory.push(buildPromotionEvent({
    from: fromState,
    to: opts.targetState,
    reason: opts.reason ?? `State changed to ${opts.targetState}`,
    evidence: opts.evidence,
  }));
}
