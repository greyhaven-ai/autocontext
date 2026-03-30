/**
 * AC-460: CUDA training backend — real training and serving path.
 *
 * Tests the training backend abstraction, CUDA and MLX implementations,
 * backend registry, training runner, and artifact publishing.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { spawnSync } from "node:child_process";
import { mkdtempSync, rmSync, existsSync, readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  TrainingBackend,
  MLXBackend,
  CUDABackend,
  BackendRegistry,
  defaultBackendRegistry,
  TrainingRunner,
  type TrainingConfig,
  type TrainingResult,
} from "../src/index.js";
import * as pkg from "../src/index.js";

let tmpDir: string;
const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-460-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

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

function runCli(args: string[]): { stdout: string; stderr: string; exitCode: number } {
  const result = spawnSync("npx", ["tsx", CLI, ...args], {
    encoding: "utf8",
    timeout: 10000,
    env: { ...process.env, NODE_NO_WARNINGS: "1" },
  });

  return {
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    exitCode: result.status ?? 1,
  };
}

// ---------------------------------------------------------------------------
// Backend abstraction
// ---------------------------------------------------------------------------

describe("TrainingBackend interface", () => {
  it("MLXBackend has correct name and metadata", () => {
    const mlx = new MLXBackend();
    expect(mlx.name).toBe("mlx");
    expect(mlx.metadata().name).toBe("mlx");
    expect(mlx.supportedRuntimeTypes()).toContain("provider");
  });

  it("CUDABackend has correct name and metadata", () => {
    const cuda = new CUDABackend();
    expect(cuda.name).toBe("cuda");
    expect(cuda.metadata().name).toBe("cuda");
    expect(cuda.supportedRuntimeTypes()).toContain("provider");
  });

  it("both backends return checkpoint dirs for scenarios", () => {
    const mlx = new MLXBackend();
    const cuda = new CUDABackend();
    expect(mlx.defaultCheckpointDir("grid_ctf")).toContain("mlx");
    expect(cuda.defaultCheckpointDir("grid_ctf")).toContain("cuda");
  });

  it("isAvailable returns a boolean for each backend", () => {
    const mlx = new MLXBackend();
    const cuda = new CUDABackend();
    expect(typeof mlx.isAvailable()).toBe("boolean");
    expect(typeof cuda.isAvailable()).toBe("boolean");
  });
});

// ---------------------------------------------------------------------------
// BackendRegistry
// ---------------------------------------------------------------------------

describe("BackendRegistry", () => {
  it("default registry contains mlx and cuda", () => {
    const registry = defaultBackendRegistry();
    expect(registry.listNames()).toContain("mlx");
    expect(registry.listNames()).toContain("cuda");
  });

  it("gets backend by name", () => {
    const registry = defaultBackendRegistry();
    expect(registry.get("mlx")).toBeDefined();
    expect(registry.get("cuda")).toBeDefined();
    expect(registry.get("nonexistent")).toBeNull();
  });

  it("lists all backends", () => {
    const registry = defaultBackendRegistry();
    const all = registry.listAll();
    expect(all.length).toBeGreaterThanOrEqual(2);
  });

  it("supports custom backend registration", () => {
    const registry = new BackendRegistry();
    registry.register(new CUDABackend());
    expect(registry.listNames()).toEqual(["cuda"]);
  });
});

// ---------------------------------------------------------------------------
// TrainingRunner
// ---------------------------------------------------------------------------

describe("TrainingRunner", () => {
  it("creates a training run with config and backend", async () => {
    const registry = new BackendRegistry();
    registry.register(new StubBackend("stub", true));
    const runner = new TrainingRunner({ registry });
    const config: TrainingConfig = {
      scenario: "grid_ctf",
      family: "game",
      datasetPath: join(tmpDir, "train.jsonl"),
      outputDir: join(tmpDir, "output"),
      backend: "stub",
      trainingMode: "from_scratch",
    };

    // Seed a minimal dataset
    writeFileSync(config.datasetPath, '{"conversations":[{"from":"human","value":"hi"}]}\n', "utf-8");

    const result = await runner.train(config);

    expect(result.status).toBe("completed");
    expect(result.backend).toBe("stub");
    expect(result.checkpointDir).toBeTruthy();
    expect(existsSync(result.checkpointDir!)).toBe(true);
  });

  it("publishes artifact with backend metadata", async () => {
    const registry = new BackendRegistry();
    registry.register(new StubBackend("cuda", true));
    const runner = new TrainingRunner({ registry });
    const config: TrainingConfig = {
      scenario: "code_review",
      family: "agent_task",
      datasetPath: join(tmpDir, "train.jsonl"),
      outputDir: join(tmpDir, "output"),
      backend: "cuda",
      trainingMode: "adapter_finetune",
      baseModel: "Qwen/Qwen3-0.6B",
    };

    writeFileSync(config.datasetPath, '{"conversations":[{"from":"human","value":"review"}]}\n', "utf-8");

    const result = await runner.train(config);
    const artifact = result.artifact!;

    expect(artifact).toBeDefined();
    expect(artifact.backend).toBe("cuda");
    expect(artifact.trainingMode).toBe("adapter_finetune");
    expect(artifact.baseModel).toBe("Qwen/Qwen3-0.6B");
    expect(artifact.scenario).toBe("code_review");
    expect(artifact.family).toBe("agent_task");
    expect(artifact.artifactId).toBeTruthy();
    expect(artifact.trainedAt).toBeTruthy();
  });

  it("handles training failure gracefully", async () => {
    const registry = new BackendRegistry();
    registry.register(new StubBackend("stub", true));
    const runner = new TrainingRunner({ registry });
    const config: TrainingConfig = {
      scenario: "test",
      family: "game",
      datasetPath: join(tmpDir, "nonexistent.jsonl"),
      outputDir: join(tmpDir, "output"),
      backend: "stub",
      trainingMode: "from_scratch",
    };

    const result = await runner.train(config);
    expect(result.status).toBe("failed");
    expect(result.error).toBeTruthy();
  });

  it("saves training manifest alongside checkpoint", async () => {
    const registry = new BackendRegistry();
    registry.register(new StubBackend("stub", true));
    const runner = new TrainingRunner({ registry });
    const config: TrainingConfig = {
      scenario: "test_manifest",
      family: "simulation",
      datasetPath: join(tmpDir, "train.jsonl"),
      outputDir: join(tmpDir, "output"),
      backend: "stub",
      trainingMode: "from_scratch",
    };

    writeFileSync(config.datasetPath, '{"conversations":[{"from":"human","value":"sim"}]}\n', "utf-8");

    const result = await runner.train(config);
    const manifestPath = join(result.checkpointDir!, "training_manifest.json");
    expect(existsSync(manifestPath)).toBe(true);

    const manifest = JSON.parse(readFileSync(manifestPath, "utf-8"));
    expect(manifest.scenario).toBe("test_manifest");
    expect(manifest.backend).toBe("stub");
    expect(manifest.trainingMode).toBe("from_scratch");
    expect(manifest.datasetSize).toBeGreaterThan(0);
  });

  it("fails when the requested backend is unknown", async () => {
    const runner = new TrainingRunner({ registry: new BackendRegistry() });
    const config: TrainingConfig = {
      scenario: "unknown_backend",
      family: "game",
      datasetPath: join(tmpDir, "train.jsonl"),
      outputDir: join(tmpDir, "output"),
      backend: "bogus",
      trainingMode: "from_scratch",
    };

    writeFileSync(config.datasetPath, '{"conversations":[{"from":"human","value":"hi"}]}\n', "utf-8");

    const result = await runner.train(config);
    expect(result.status).toBe("failed");
    expect(result.error).toContain("Unknown training backend");
  });

  it("fails when the requested backend is unavailable", async () => {
    const registry = new BackendRegistry();
    registry.register(new StubBackend("stub", false));
    const runner = new TrainingRunner({ registry });
    const config: TrainingConfig = {
      scenario: "unavailable_backend",
      family: "game",
      datasetPath: join(tmpDir, "train.jsonl"),
      outputDir: join(tmpDir, "output"),
      backend: "stub",
      trainingMode: "from_scratch",
    };

    writeFileSync(config.datasetPath, '{"conversations":[{"from":"human","value":"hi"}]}\n', "utf-8");

    const result = await runner.train(config);
    expect(result.status).toBe("failed");
    expect(result.error).toContain("not available");
  });

  it("delegates execution to an injected real executor hook", async () => {
    const registry = new BackendRegistry();
    registry.register(new StubBackend("cuda", true));
    let executorCalls = 0;
    const runner = new TrainingRunner({
      registry,
      executor: async (config, checkpointDir) => {
        executorCalls += 1;
        expect(config.backend).toBe("cuda");
        expect(checkpointDir).toContain(join("models", "executor_hook", "cuda"));
        return { success: true, metrics: { loss: 0.12 } };
      },
    });
    const config: TrainingConfig = {
      scenario: "executor_hook",
      family: "agent_task",
      datasetPath: join(tmpDir, "train.jsonl"),
      outputDir: join(tmpDir, "output"),
      backend: "cuda",
      trainingMode: "adapter_finetune",
    };

    writeFileSync(config.datasetPath, '{"conversations":[{"from":"human","value":"hi"}]}\n', "utf-8");

    const result = await runner.train(config);
    expect(result.status).toBe("completed");
    expect(executorCalls).toBe(1);
    expect(result.artifact?.metrics?.loss).toBe(0.12);
  });

  it("derives a default base model for non-from-scratch strategies", async () => {
    const registry = new BackendRegistry();
    registry.register(new StubBackend("cuda", true));
    const runner = new TrainingRunner({ registry });
    const config: TrainingConfig = {
      scenario: "default_base_model",
      family: "agent_task",
      datasetPath: join(tmpDir, "train.jsonl"),
      outputDir: join(tmpDir, "output"),
      backend: "cuda",
      trainingMode: "adapter_finetune",
    };

    writeFileSync(config.datasetPath, '{"conversations":[{"from":"human","value":"hi"}]}\n', "utf-8");

    const result = await runner.train(config);
    expect(result.status).toBe("completed");
    expect(result.artifact?.baseModel).toBe("Qwen/Qwen3-0.6B");
  });

  it("fails when the selected base model is unknown", async () => {
    const registry = new BackendRegistry();
    registry.register(new StubBackend("cuda", true));
    const runner = new TrainingRunner({ registry });
    const config: TrainingConfig = {
      scenario: "invalid_base_model",
      family: "agent_task",
      datasetPath: join(tmpDir, "train.jsonl"),
      outputDir: join(tmpDir, "output"),
      backend: "cuda",
      trainingMode: "adapter_finetune",
      baseModel: "unknown/model",
    };

    writeFileSync(config.datasetPath, '{"conversations":[{"from":"human","value":"hi"}]}\n', "utf-8");

    const result = await runner.train(config);
    expect(result.status).toBe("failed");
    expect(result.error).toContain("known model registry");
  });
});

// ---------------------------------------------------------------------------
// TrainingResult shape
// ---------------------------------------------------------------------------

describe("TrainingResult shape", () => {
  it("has all required fields", async () => {
    const registry = new BackendRegistry();
    registry.register(new StubBackend("stub", true));
    const runner = new TrainingRunner({ registry });
    writeFileSync(join(tmpDir, "train.jsonl"), '{"conversations":[]}\n', "utf-8");

    const result: TrainingResult = await runner.train({
      scenario: "shape_test",
      family: "game",
      datasetPath: join(tmpDir, "train.jsonl"),
      outputDir: join(tmpDir, "output"),
      backend: "stub",
      trainingMode: "from_scratch",
    });

    expect(result).toHaveProperty("status");
    expect(result).toHaveProperty("backend");
    expect(result).toHaveProperty("checkpointDir");
    expect(result).toHaveProperty("artifact");
    expect(result).toHaveProperty("durationMs");
  });
});

describe("public package surface", () => {
  it("exports the training backend APIs from the root entrypoint", () => {
    expect(pkg.TrainingBackend).toBeDefined();
    expect(pkg.MLXBackend).toBeDefined();
    expect(pkg.CUDABackend).toBeDefined();
    expect(pkg.BackendRegistry).toBeDefined();
    expect(pkg.defaultBackendRegistry).toBeDefined();
    expect(pkg.TrainingRunner).toBeDefined();
  });
});

describe("train CLI", () => {
  it("fails clearly when only the synthetic default executor is available", () => {
    const datasetPath = join(tmpDir, "train.jsonl");
    writeFileSync(datasetPath, '{"conversations":[{"from":"human","value":"hi"}]}\n', "utf-8");

    const result = runCli([
      "train",
      "--scenario",
      "cli_train",
      "--dataset",
      datasetPath,
      "--backend",
      "cuda",
    ]);

    expect(result.exitCode).toBe(1);
    expect(`${result.stdout}\n${result.stderr}`).toContain("no real training executor is configured");
  });
});
