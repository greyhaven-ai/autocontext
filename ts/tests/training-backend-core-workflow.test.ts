import { describe, expect, it } from "vitest";
import { join } from "node:path";

import {
  BackendRegistry,
  CUDABackend,
  defaultBackendRegistry,
  MLXBackend,
  TrainingBackend,
} from "../src/training/training-backend-core.js";

class StubBackend extends TrainingBackend {
  constructor(
    readonly name: string,
    private readonly available: boolean,
  ) {
    super();
  }

  isAvailable(): boolean {
    return this.available;
  }

  defaultCheckpointDir(scenario: string): string {
    return join("models", scenario, this.name);
  }
}

describe("training backend core workflow", () => {
  it("exposes backend metadata and runtime support", () => {
    const mlx = new MLXBackend();
    const cuda = new CUDABackend();

    expect(mlx.metadata()).toMatchObject({ name: "mlx" });
    expect(mlx.supportedRuntimeTypes()).toContain("pi");
    expect(cuda.metadata()).toMatchObject({ name: "cuda" });
  });

  it("registers and lists backends through the registry", () => {
    const registry = new BackendRegistry();
    registry.register(new StubBackend("stub", true));

    expect(registry.get("stub")?.name).toBe("stub");
    expect(registry.listNames()).toEqual(["stub"]);
    expect(defaultBackendRegistry().listNames()).toEqual(["cuda", "mlx"]);
  });
});
