/**
 * Training backend abstraction with CUDA and MLX implementations (AC-460).
 *
 * Ports Python's autocontext/training/backends.py to TypeScript and adds
 * a TrainingRunner that connects backends to the model strategy (AC-459).
 *
 * This makes CUDA a real training path, not just a registry entry.
 */

import {
  BackendRegistry,
  CUDABackend,
  defaultBackendRegistry,
  MLXBackend,
  TrainingBackend,
} from "./training-backend-core.js";
import { defaultExecutor } from "./training-runner-workflow.js";
import { executeTrainingRunWorkflow } from "./training-run-execution-workflow.js";
import type { TrainingExecutor } from "./training-types.js";
import {
  ModelRegistry,
  PromotionEngine,
  type ModelRecord,
} from "./promotion.js";
import type {
  PublishedArtifact,
  TrainingConfig,
  TrainingResult,
} from "./training-types.js";

export {
  BackendRegistry,
  CUDABackend,
  defaultBackendRegistry,
  MLXBackend,
  TrainingBackend,
};
export type {
  PublishedArtifact,
  TrainingConfig,
  TrainingResult,
} from "./training-types.js";
export type { TrainingExecutor } from "./training-types.js";

export class TrainingRunner {
  private registry: BackendRegistry;
  private executor: TrainingExecutor;
  private promotionRegistry: ModelRegistry;
  private promotionEngine: PromotionEngine;

  constructor(opts?: {
    registry?: BackendRegistry;
    executor?: TrainingExecutor;
    promotionRegistry?: ModelRegistry;
    promotionEngine?: PromotionEngine;
  }) {
    this.registry = opts?.registry ?? defaultBackendRegistry();
    this.executor = opts?.executor ?? defaultExecutor;
    this.promotionRegistry = opts?.promotionRegistry ?? new ModelRegistry();
    this.promotionEngine = opts?.promotionEngine ?? new PromotionEngine();
  }

  usesSyntheticExecutor(): boolean {
    return this.executor === defaultExecutor;
  }

  getPromotionRegistry(): ModelRegistry {
    return this.promotionRegistry;
  }

  getPromotionEngine(): PromotionEngine {
    return this.promotionEngine;
  }

  getModelRecord(artifactId: string): ModelRecord | null {
    return this.promotionRegistry.get(artifactId);
  }

  async train(config: TrainingConfig): Promise<TrainingResult> {
    return executeTrainingRunWorkflow({
      start: performance.now(),
      config,
      registry: this.registry,
      executor: this.executor,
      promotionRegistry: this.promotionRegistry,
      promotionEngine: this.promotionEngine,
    });
  }
}
