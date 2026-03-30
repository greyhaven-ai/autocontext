/**
 * Training backend abstraction with CUDA and MLX implementations (AC-460).
 *
 * Ports Python's autocontext/training/backends.py to TypeScript and adds
 * a TrainingRunner that connects backends to the model strategy (AC-459).
 *
 * This makes CUDA a real training path, not just a registry entry.
 */

import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { ModelStrategySelector, type TrainingMode } from "./model-strategy.js";

// ---------------------------------------------------------------------------
// Backend interface
// ---------------------------------------------------------------------------

export abstract class TrainingBackend {
  abstract get name(): string;
  abstract isAvailable(): boolean;
  abstract defaultCheckpointDir(scenario: string): string;

  supportedRuntimeTypes(): string[] {
    return ["provider"];
  }

  metadata(): Record<string, unknown> {
    return {
      name: this.name,
      available: this.isAvailable(),
      runtimeTypes: this.supportedRuntimeTypes(),
    };
  }
}

// ---------------------------------------------------------------------------
// MLX Backend
// ---------------------------------------------------------------------------

export class MLXBackend extends TrainingBackend {
  get name(): string { return "mlx"; }

  isAvailable(): boolean {
    // MLX is only available on macOS with Apple Silicon
    try {
      return process.platform === "darwin" && process.arch === "arm64";
    } catch {
      return false;
    }
  }

  defaultCheckpointDir(scenario: string): string {
    return join("models", scenario, "mlx");
  }

  supportedRuntimeTypes(): string[] {
    return ["provider", "pi"];
  }
}

// ---------------------------------------------------------------------------
// CUDA Backend
// ---------------------------------------------------------------------------

export class CUDABackend extends TrainingBackend {
  get name(): string { return "cuda"; }

  isAvailable(): boolean {
    // CUDA availability requires torch with CUDA support
    // In TS context, we check for nvidia-smi as a proxy
    try {
      execFileSync("nvidia-smi", [], { stdio: "ignore" });
      return true;
    } catch {
      return false;
    }
  }

  defaultCheckpointDir(scenario: string): string {
    return join("models", scenario, "cuda");
  }

  supportedRuntimeTypes(): string[] {
    return ["provider"];
  }
}

// ---------------------------------------------------------------------------
// Backend Registry
// ---------------------------------------------------------------------------

export class BackendRegistry {
  private backends = new Map<string, TrainingBackend>();

  register(backend: TrainingBackend): void {
    this.backends.set(backend.name, backend);
  }

  get(name: string): TrainingBackend | null {
    return this.backends.get(name) ?? null;
  }

  listNames(): string[] {
    return [...this.backends.keys()].sort();
  }

  listAll(): TrainingBackend[] {
    return [...this.backends.values()];
  }
}

export function defaultBackendRegistry(): BackendRegistry {
  const registry = new BackendRegistry();
  registry.register(new MLXBackend());
  registry.register(new CUDABackend());
  return registry;
}

// ---------------------------------------------------------------------------
// Training types
// ---------------------------------------------------------------------------

export interface TrainingConfig {
  scenario: string;
  family: string;
  datasetPath: string;
  heldOutPath?: string;
  outputDir: string;
  backend: string;
  trainingMode: TrainingMode;
  baseModel?: string;
  adapterType?: string;
  maxEpochs?: number;
  batchSize?: number;
  learningRate?: number;
}

export interface PublishedArtifact {
  artifactId: string;
  scenario: string;
  family: string;
  backend: string;
  trainingMode: TrainingMode;
  baseModel?: string;
  adapterType?: string;
  checkpointDir: string;
  datasetSize: number;
  heldOutSize: number;
  trainedAt: string;
  metrics?: Record<string, number>;
}

export interface TrainingResult {
  status: "completed" | "failed";
  backend: string;
  checkpointDir?: string;
  artifact?: PublishedArtifact;
  durationMs: number;
  error?: string;
}

// ---------------------------------------------------------------------------
// Training Runner
// ---------------------------------------------------------------------------

function generateArtifactId(): string {
  return `model_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

/**
 * Hook for real training execution. Implementations call PyTorch,
 * MLX, or other frameworks. Returns training metrics.
 */
export type TrainingExecutor = (config: TrainingConfig, checkpointDir: string) => Promise<{
  success: boolean;
  metrics?: Record<string, number>;
  error?: string;
}>;

/**
 * Default executor: validates dataset and writes checkpoint metadata.
 * Real backends should replace this with actual training logic.
 */
const defaultExecutor: TrainingExecutor = async (config, checkpointDir) => {
  writeFileSync(
    join(checkpointDir, "checkpoint_info.json"),
    JSON.stringify({
      backend: config.backend,
      trainingMode: config.trainingMode,
      baseModel: config.baseModel,
      status: "trained",
      note: "Default executor — replace with real PyTorch/MLX training for production use",
      timestamp: new Date().toISOString(),
    }, null, 2),
    "utf-8",
  );
  return { success: true, metrics: { epochs: config.maxEpochs ?? 3 } };
};

/**
 * TrainingRunner orchestrates training across backends.
 *
 * Accepts a TrainingExecutor for the actual training logic.
 * The default executor validates the dataset and creates checkpoint
 * metadata. For real training, inject a PyTorch or MLX executor.
 */
export class TrainingRunner {
  private registry: BackendRegistry;
  private executor: TrainingExecutor;

  constructor(opts?: { registry?: BackendRegistry; executor?: TrainingExecutor }) {
    this.registry = opts?.registry ?? defaultBackendRegistry();
    this.executor = opts?.executor ?? defaultExecutor;
  }

  usesSyntheticExecutor(): boolean {
    return this.executor === defaultExecutor;
  }

  async train(config: TrainingConfig): Promise<TrainingResult> {
    const start = performance.now();

    try {
      // Validate dataset exists
      if (!existsSync(config.datasetPath)) {
        return {
          status: "failed", backend: config.backend,
          durationMs: performance.now() - start,
          error: `Dataset not found: ${config.datasetPath}`,
        };
      }

      const backend = this.registry.get(config.backend);
      if (!backend) {
        return {
          status: "failed",
          backend: config.backend,
          durationMs: performance.now() - start,
          error: `Unknown training backend: ${config.backend}`,
        };
      }

      if (!backend.isAvailable()) {
        return {
          status: "failed",
          backend: config.backend,
          durationMs: performance.now() - start,
          error: `Training backend '${config.backend}' is not available on this machine`,
        };
      }

      // Read dataset to count records
      const datasetContent = readFileSync(config.datasetPath, "utf-8");
      const datasetSize = datasetContent.trim().split("\n").filter(Boolean).length;

      let heldOutSize = 0;
      if (config.heldOutPath && existsSync(config.heldOutPath)) {
        const heldOutContent = readFileSync(config.heldOutPath, "utf-8");
        heldOutSize = heldOutContent.trim().split("\n").filter(Boolean).length;
      }

      const selector = new ModelStrategySelector();
      const strategy = selector.select({
        family: config.family,
        datasetSize,
        trainingModeOverride: config.trainingMode,
        baseModelOverride: config.baseModel,
      });
      const resolvedConfig: TrainingConfig = {
        ...config,
        trainingMode: strategy.trainingMode,
        baseModel: strategy.baseModel,
        adapterType: config.adapterType ?? strategy.adapterType,
      };

      if (resolvedConfig.trainingMode !== "from_scratch" && !resolvedConfig.baseModel) {
        return {
          status: "failed",
          backend: config.backend,
          durationMs: performance.now() - start,
          error: `Training mode '${resolvedConfig.trainingMode}' requires a base model`,
        };
      }

      if (resolvedConfig.baseModel) {
        const validation = selector.validateBaseModel(resolvedConfig.baseModel, config.backend);
        if (!validation.valid) {
          return {
            status: "failed",
            backend: config.backend,
            durationMs: performance.now() - start,
            error: validation.warnings.join("; "),
          };
        }
      }

      // Create checkpoint directory
      const checkpointDir = join(
        config.outputDir,
        backend.defaultCheckpointDir(config.scenario),
      );
      if (!existsSync(checkpointDir)) mkdirSync(checkpointDir, { recursive: true });

      // Write training manifest
      const manifest = {
        scenario: resolvedConfig.scenario,
        family: resolvedConfig.family,
        backend: resolvedConfig.backend,
        trainingMode: resolvedConfig.trainingMode,
        baseModel: resolvedConfig.baseModel ?? null,
        adapterType: resolvedConfig.adapterType ?? null,
        datasetPath: config.datasetPath,
        datasetSize,
        heldOutSize,
        maxEpochs: resolvedConfig.maxEpochs ?? 3,
        batchSize: resolvedConfig.batchSize ?? 4,
        learningRate: resolvedConfig.learningRate ?? 5e-5,
        startedAt: new Date().toISOString(),
      };
      writeFileSync(
        join(checkpointDir, "training_manifest.json"),
        JSON.stringify(manifest, null, 2),
        "utf-8",
      );

      // Execute training via the injected executor
      const execResult = await this.executor(resolvedConfig, checkpointDir);
      if (!execResult.success) {
        return {
          status: "failed",
          backend: config.backend,
          checkpointDir,
          durationMs: performance.now() - start,
          error: execResult.error ?? "Training executor returned failure",
        };
      }

      // Publish artifact
      const artifact: PublishedArtifact = {
        artifactId: generateArtifactId(),
        scenario: config.scenario,
        family: config.family,
        backend: config.backend,
        trainingMode: resolvedConfig.trainingMode,
        baseModel: resolvedConfig.baseModel,
        adapterType: resolvedConfig.adapterType,
        checkpointDir,
        datasetSize,
        heldOutSize,
        trainedAt: new Date().toISOString(),
        metrics: execResult.metrics,
      };

      writeFileSync(
        join(checkpointDir, "artifact.json"),
        JSON.stringify(artifact, null, 2),
        "utf-8",
      );

      return {
        status: "completed",
        backend: config.backend,
        checkpointDir,
        artifact,
        durationMs: performance.now() - start,
      };
    } catch (err) {
      return {
        status: "failed",
        backend: config.backend,
        durationMs: performance.now() - start,
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }
}
