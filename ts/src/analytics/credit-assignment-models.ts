import type {
  AttributionResultDict,
  ComponentChangeDict,
  CreditAssignmentRecordDict,
  GenerationChangeVectorDict,
} from "./credit-assignment-contracts.js";
import {
  buildAttributionResultDict,
  buildComponentChangeDict,
  buildCreditAssignmentRecordDict,
  buildGenerationChangeVectorDict,
  computeTotalChangeMagnitude,
  normalizeAttributionResultData,
  normalizeComponentChangeData,
  normalizeCreditAssignmentRecordData,
  normalizeGenerationChangeVectorData,
} from "./credit-assignment-serialization-workflow.js";

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

  toDict(): ComponentChangeDict {
    return buildComponentChangeDict(this);
  }

  static fromDict(data: Record<string, unknown>): ComponentChange {
    const normalized = normalizeComponentChangeData(data);
    return new ComponentChange(
      normalized.component,
      normalized.magnitude,
      normalized.description,
      normalized.metadata,
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
    return computeTotalChangeMagnitude(this.changes);
  }

  toDict(): GenerationChangeVectorDict {
    return buildGenerationChangeVectorDict(this);
  }

  static fromDict(data: Record<string, unknown>): GenerationChangeVector {
    const normalized = normalizeGenerationChangeVectorData(data);
    return new GenerationChangeVector(
      normalized.generation,
      normalized.scoreDelta,
      normalized.changes.map((change) => ComponentChange.fromDict(change)),
      normalized.metadata,
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

  toDict(): AttributionResultDict {
    return buildAttributionResultDict(this);
  }

  static fromDict(data: Record<string, unknown>): AttributionResult {
    const normalized = normalizeAttributionResultData(data);
    return new AttributionResult(
      normalized.generation,
      normalized.totalDelta,
      normalized.credits,
      normalized.metadata,
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

  toDict(): CreditAssignmentRecordDict {
    return buildCreditAssignmentRecordDict(this);
  }

  static fromDict(data: Record<string, unknown>): CreditAssignmentRecord {
    const normalized = normalizeCreditAssignmentRecordData(data);
    return new CreditAssignmentRecord(
      normalized.runId,
      normalized.generation,
      GenerationChangeVector.fromDict(normalized.vector),
      AttributionResult.fromDict(normalized.attribution),
      normalized.metadata,
    );
  }
}
