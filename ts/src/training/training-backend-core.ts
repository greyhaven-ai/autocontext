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
  get name(): string { return "mlx"; }

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

export class CUDABackend extends TrainingBackend {
  get name(): string { return "cuda"; }

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
  registry.register(new CUDABackend());
  return registry;
}
