/**
 * Component sensitivity profiling and credit assignment.
 *
 * TS port of autocontext.analytics.credit_assignment (AC-381).
 */

import { buildAttributedCredits } from "./credit-assignment-attribution-workflow.js";
import {
  recordAttributedCredits,
  recordContributionDelta,
  summarizeContributionCredits,
} from "./credit-assignment-contribution-workflow.js";
import {
  AttributionResult,
  ComponentChange,
  CreditAssignmentRecord,
  GenerationChangeVector,
} from "./credit-assignment-models.js";
import {
  formatAttributionForAgent as formatAttributionForAgentReport,
  summarizeCreditPatterns as summarizeCreditPatternsReport,
} from "./credit-assignment-reporting.js";
import type {
  AttributionResultDict,
  ComponentChangeDict,
  CreditAssignmentRecordDict,
  CreditPatternSummary,
  GenerationChangeVectorDict,
} from "./credit-assignment-contracts.js";
import { computeGenerationChangeVector } from "./credit-assignment-vector-workflow.js";

export type {
  AttributionResultDict,
  ComponentChangeDict,
  CreditAssignmentRecordDict,
  CreditPatternComponentSummary,
  CreditPatternSummary,
  GenerationChangeVectorDict,
} from "./credit-assignment-contracts.js";
export {
  AttributionResult,
  ComponentChange,
  CreditAssignmentRecord,
  GenerationChangeVector,
} from "./credit-assignment-models.js";

export function computeChangeVector(
  generation: number,
  scoreDelta: number,
  previousState: Record<string, unknown>,
  currentState: Record<string, unknown>,
): GenerationChangeVector {
  return computeGenerationChangeVector(
    generation,
    scoreDelta,
    previousState,
    currentState,
  );
}

export function attributeCredit(vector: GenerationChangeVector): AttributionResult {
  return new AttributionResult(
    vector.generation,
    vector.scoreDelta,
    buildAttributedCredits(vector),
  );
}

export function formatAttributionForAgent(result: AttributionResult, role: string): string {
  return formatAttributionForAgentReport(result, role);
}

export function summarizeCreditPatterns(records: CreditAssignmentRecord[]): CreditPatternSummary {
  return summarizeCreditPatternsReport(records);
}

export class CreditAssigner {
  #contributions: Map<string, number[]> = new Map();

  recordContribution(component: string, scoreDelta: number): void {
    recordContributionDelta(this.#contributions, component, scoreDelta);
  }

  getCredits(): Record<string, number> {
    return summarizeContributionCredits(this.#contributions);
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
    recordAttributedCredits(this.#contributions, attribution.credits);
    return attribution;
  }

  formatAttributionForAgent(result: AttributionResult, role: string): string {
    return formatAttributionForAgentReport(result, role);
  }

  summarizeCreditPatterns(records: CreditAssignmentRecord[]): CreditPatternSummary {
    return summarizeCreditPatternsReport(records);
  }
}
