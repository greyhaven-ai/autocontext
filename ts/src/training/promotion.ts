/**
 * Candidate-shadow-active promotion lifecycle (AC-456).
 *
 * Staged deployment pipeline for distilled models:
 *   candidate → shadow → active
 *
 * A model only becomes the live default after proving itself:
 * 1. candidate: just trained, passes held-out eval
 * 2. shadow: runs alongside incumbent, scores compared
 * 3. active: promoted to live default after shadow validation
 *
 * Automatic rollback on parse/validation/score regressions.
 */

import {
  applyModelStateTransition,
  createModelRecord,
  listModelRecordsForScenario,
  resolveActiveModelRecord,
} from "./promotion-registry-workflow.js";
import {
  buildShadowPromotionCheck,
  evaluatePromotionCheck,
  normalizePromotionThresholds,
} from "./promotion-engine-workflow.js";
import type {
  ActivationState,
  ModelRecord,
  PromotionCheck,
  PromotionDecision,
  PromotionThresholds,
  ShadowExecutor,
  ShadowRunOpts,
} from "./promotion-types.js";

export {
  ACTIVATION_STATES,
} from "./promotion-types.js";
export type {
  ActivationState,
  ModelRecord,
  PromotionCheck,
  PromotionDecision,
  PromotionEvent,
  PromotionThresholds,
  ShadowExecutor,
  ShadowRunOpts,
} from "./promotion-types.js";

export class ModelRegistry {
  private records = new Map<string, ModelRecord>();

  register(opts: {
    scenario: string;
    family: string;
    backend: string;
    checkpointDir: string;
    activationState?: ActivationState;
  }): string {
    const record = createModelRecord(opts);
    this.records.set(record.artifactId, record);
    return record.artifactId;
  }

  get(id: string): ModelRecord | null {
    return this.records.get(id) ?? null;
  }

  listForScenario(scenario: string): ModelRecord[] {
    return listModelRecordsForScenario(this.records.values(), scenario);
  }

  resolveActive(scenario: string): ModelRecord | null {
    return resolveActiveModelRecord(this.records.values(), scenario);
  }

  setState(
    id: string,
    state: ActivationState,
    opts?: { reason?: string; evidence?: Record<string, unknown> },
  ): void {
    applyModelStateTransition({
      records: this.records,
      artifactId: id,
      targetState: state,
      reason: opts?.reason,
      evidence: opts?.evidence,
    });
  }

  listAll(): ModelRecord[] {
    return [...this.records.values()];
  }
}

export class PromotionEngine {
  private thresholds: PromotionThresholds;
  private shadowExecutor?: ShadowExecutor;

  constructor(opts?: { thresholds?: Partial<PromotionThresholds>; shadowExecutor?: ShadowExecutor }) {
    this.thresholds = normalizePromotionThresholds(opts?.thresholds);
    this.shadowExecutor = opts?.shadowExecutor;
  }

  async runShadow(
    artifactId: string,
    scenario: string,
    opts: ShadowRunOpts,
  ): Promise<PromotionCheck | null> {
    return buildShadowPromotionCheck({
      artifactId,
      scenario,
      shadowExecutor: this.shadowExecutor,
      run: opts,
    });
  }

  evaluate(check: PromotionCheck): PromotionDecision {
    return evaluatePromotionCheck(check, this.thresholds);
  }
}
