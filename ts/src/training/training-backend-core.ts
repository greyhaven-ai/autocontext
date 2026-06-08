import process from "node:process";
import { execFileSync } from "node:child_process";
import { join } from "node:path";

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

export class MLXBackend extends TrainingBackend {
  get name(): string {
    return "mlx";
  }

  isAvailable(): boolean {
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

/**
 * mlx-lm LoRA finetuning (SFT / reasoning-distillation cold-start). Apple Silicon only;
 * shells out to the Python `mlxlm` backend. The distillation half of the R1 recipe.
 */
export class MLXLMBackend extends TrainingBackend {
  get name(): string {
    return "mlxlm";
  }

  isAvailable(): boolean {
    try {
      return process.platform === "darwin" && process.arch === "arm64";
    } catch {
      return false;
    }
  }

  defaultCheckpointDir(scenario: string): string {
    return join("models", scenario, "mlxlm");
  }

  supportedRuntimeTypes(): string[] {
    return ["provider", "pi"];
  }
}

/**
 * GRPO/GSPO RLVR (online RL from verifiable rewards). Apple Silicon only; shells out to
 * the Python `grpo` backend (mlx-lm-lora). The RLVR half of the R1 recipe; can resume
 * from an MLXLM-distilled adapter for the full distill -> RLVR pipeline.
 */
export class GRPOBackend extends TrainingBackend {
  get name(): string {
    return "grpo";
  }

  isAvailable(): boolean {
    try {
      return process.platform === "darwin" && process.arch === "arm64";
    } catch {
      return false;
    }
  }

  defaultCheckpointDir(scenario: string): string {
    return join("models", scenario, "grpo");
  }

  supportedRuntimeTypes(): string[] {
    return ["provider", "pi"];
  }
}

/**
 * On-policy distillation (dense per-token reverse-KL from a teacher). Apple Silicon only;
 * shells out to the Python `opd` backend (mlx-lm, in-process trainer). The MLX-native
 * counterpart to TRL's GKDTrainer; a CUDA/TRL path is the cross-platform route for larger runs.
 */
export class OnPolicyDistillBackend extends TrainingBackend {
  get name(): string {
    return "opd";
  }

  isAvailable(): boolean {
    try {
      return process.platform === "darwin" && process.arch === "arm64";
    } catch {
      return false;
    }
  }

  defaultCheckpointDir(scenario: string): string {
    return join("models", scenario, "opd");
  }

  supportedRuntimeTypes(): string[] {
    return ["provider", "pi"];
  }
}

/**
 * Cross-platform TRL backend: on-policy distillation (GKD) + RLVR (GRPO) via HuggingFace TRL.
 * Unlike the MLX backends this is not Apple-Silicon-locked; it shells out to the Python `trl`
 * backend (needs trl + torch), the path for larger / non-Mac runs. Availability is enforced
 * Python-side at runtime, so this reports available as an orchestration option everywhere.
 */
export class TRLBackend extends TrainingBackend {
  get name(): string {
    return "trl";
  }

  isAvailable(): boolean {
    return true;
  }

  defaultCheckpointDir(scenario: string): string {
    return join("models", scenario, "trl");
  }

  supportedRuntimeTypes(): string[] {
    return ["provider"];
  }
}

export class CUDABackend extends TrainingBackend {
  get name(): string {
    return "cuda";
  }

  isAvailable(): boolean {
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
}

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
  registry.register(new MLXLMBackend());
  registry.register(new GRPOBackend());
  registry.register(new OnPolicyDistillBackend());
  registry.register(new TRLBackend());
  registry.register(new CUDABackend());
  return registry;
}
