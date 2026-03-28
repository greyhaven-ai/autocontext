/**
 * AC-452: simulate export — portable simulation result packages.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { SimulationEngine } from "../src/simulation/engine.js";
import { exportSimulation, type SimulationExportResult } from "../src/simulation/export.js";
import type { LLMProvider } from "../src/types/index.js";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");
const SANITIZED_KEYS = [
  "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AUTOCONTEXT_API_KEY",
  "AUTOCONTEXT_AGENT_API_KEY", "AUTOCONTEXT_PROVIDER", "AUTOCONTEXT_AGENT_PROVIDER",
  "AUTOCONTEXT_DB_PATH", "AUTOCONTEXT_RUNS_ROOT", "AUTOCONTEXT_KNOWLEDGE_ROOT",
  "AUTOCONTEXT_CONFIG_DIR", "AUTOCONTEXT_AGENT_DEFAULT_MODEL", "AUTOCONTEXT_MODEL",
];

function mockProvider(): LLMProvider {
  const spec = JSON.stringify({
    description: "Export test simulation",
    environment_description: "Env",
    initial_state_description: "Start",
    success_criteria: ["done"],
    failure_modes: ["timeout"],
    max_steps: 10,
    actions: [
      { name: "step_a", description: "A", parameters: {}, preconditions: [], effects: ["a_done"] },
      { name: "step_b", description: "B", parameters: {}, preconditions: ["step_a"], effects: ["b_done"] },
    ],
  });
  return {
    complete: async () => ({ text: spec }),
    defaultModel: () => "test-model",
  } as unknown as LLMProvider;
}

function buildEnv(overrides: Record<string, string> = {}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, NODE_NO_WARNINGS: "1" };
  for (const key of SANITIZED_KEYS) delete env[key];
  return { ...env, ...overrides };
}

let tmpDir: string;

beforeEach(() => {
  tmpDir = mkdtempSync(join(tmpdir(), "ac-452-test-"));
});
afterEach(() => {
  rmSync(tmpDir, { recursive: true, force: true });
});

// ---------------------------------------------------------------------------
// JSON export
// ---------------------------------------------------------------------------

describe("simulate export — JSON", () => {
  it("exports a saved simulation as a portable JSON package", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);
    await engine.run({ description: "JSON export test", saveAs: "json_test" });

    const result = exportSimulation({
      id: "json_test",
      knowledgeRoot: tmpDir,
      format: "json",
    });

    expect(result.status).toBe("completed");
    expect(result.outputPath).toBeTruthy();
    expect(existsSync(result.outputPath!)).toBe(true);

    const pkg = JSON.parse(readFileSync(result.outputPath!, "utf-8"));
    expect(pkg.name).toBe("json_test");
    expect(pkg.spec).toBeDefined();
    expect(pkg.results).toBeDefined();
    expect(pkg.assumptions).toBeDefined();
    expect(pkg.variables).toBeDefined();
  });

  it("JSON package includes all assumptions and warnings", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);
    await engine.run({ description: "Assumptions test", saveAs: "assume_test" });

    const result = exportSimulation({ id: "assume_test", knowledgeRoot: tmpDir, format: "json" });
    const pkg = JSON.parse(readFileSync(result.outputPath!, "utf-8"));

    expect(Array.isArray(pkg.assumptions)).toBe(true);
    expect(pkg.assumptions.length).toBeGreaterThan(0);
    expect(Array.isArray(pkg.warnings)).toBe(true);
    expect(pkg.warnings.length).toBeGreaterThan(0);
  });

  it("exports replay results by replay id", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);
    await engine.run({ description: "Replay export test", saveAs: "replay_base", variables: { max_steps: 2 } });
    const replay = await engine.replay({ id: "replay_base", variables: { max_steps: 1 } });

    const result = exportSimulation({
      id: replay.id,
      knowledgeRoot: tmpDir,
      format: "json",
    });

    expect(result.status).toBe("completed");
    expect(result.outputPath).toContain(replay.id);
    const pkg = JSON.parse(readFileSync(result.outputPath!, "utf-8"));
    expect(pkg.id).toBe(replay.id);
    expect(pkg.replayOf).toBe("replay_base");
  });
});

// ---------------------------------------------------------------------------
// Markdown export
// ---------------------------------------------------------------------------

describe("simulate export — Markdown", () => {
  it("exports a saved simulation as a markdown report", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);
    await engine.run({ description: "Markdown export test", saveAs: "md_test" });

    const result = exportSimulation({
      id: "md_test",
      knowledgeRoot: tmpDir,
      format: "markdown",
    });

    expect(result.status).toBe("completed");
    expect(result.outputPath).toBeTruthy();
    expect(result.outputPath!.endsWith(".md")).toBe(true);
    expect(existsSync(result.outputPath!)).toBe(true);

    const content = readFileSync(result.outputPath!, "utf-8");
    expect(content).toContain("# Simulation Report");
    expect(content).toContain("md_test");
    expect(content).toContain("Assumptions");
    expect(content).toContain("Warnings");
  });

  it("markdown includes score and dimension scores", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);
    await engine.run({ description: "Score report", saveAs: "score_md" });

    const result = exportSimulation({ id: "score_md", knowledgeRoot: tmpDir, format: "markdown" });
    const content = readFileSync(result.outputPath!, "utf-8");

    expect(content).toContain("Score");
    expect(content).toMatch(/\d+\.\d+/); // has numeric scores
  });
});

// ---------------------------------------------------------------------------
// CSV export (sweep data)
// ---------------------------------------------------------------------------

describe("simulate export — CSV", () => {
  it("exports sweep data as CSV", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);
    await engine.run({
      description: "CSV test",
      saveAs: "csv_test",
      sweep: [{ name: "seed", values: [1, 2, 3] }],
    });

    const result = exportSimulation({
      id: "csv_test",
      knowledgeRoot: tmpDir,
      format: "csv",
    });

    expect(result.status).toBe("completed");
    expect(result.outputPath!.endsWith(".csv")).toBe(true);
    expect(existsSync(result.outputPath!)).toBe(true);

    const content = readFileSync(result.outputPath!, "utf-8");
    const lines = content.trim().split("\n");
    expect(lines.length).toBeGreaterThanOrEqual(2); // header + at least 1 row
    expect(lines[0]).toContain("score"); // header has score column
    expect(lines[0]).toContain("seed");
    expect(lines.slice(1).some((line) => line.startsWith("1,"))).toBe(true);
  });

  it("CSV for non-sweep sim still works (single row)", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);
    await engine.run({ description: "Single CSV", saveAs: "single_csv" });

    const result = exportSimulation({ id: "single_csv", knowledgeRoot: tmpDir, format: "csv" });

    expect(result.status).toBe("completed");
    const lines = readFileSync(result.outputPath!, "utf-8").trim().split("\n");
    expect(lines.length).toBe(2); // header + 1 data row
  });
});

// ---------------------------------------------------------------------------
// Error handling
// ---------------------------------------------------------------------------

describe("simulate export — errors", () => {
  it("fails for nonexistent simulation", () => {
    const result = exportSimulation({ id: "nope", knowledgeRoot: tmpDir, format: "json" });
    expect(result.status).toBe("failed");
    expect(result.error).toContain("not found");
  });

  it("defaults to JSON format when not specified", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);
    await engine.run({ description: "Default format", saveAs: "default_fmt" });

    const result = exportSimulation({ id: "default_fmt", knowledgeRoot: tmpDir });
    expect(result.status).toBe("completed");
    expect(result.outputPath!.endsWith(".json")).toBe(true);
  });

  it("fails cleanly for unsupported formats when called programmatically", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);
    await engine.run({ description: "Bad format", saveAs: "bad_fmt" });

    const result = exportSimulation({
      id: "bad_fmt",
      knowledgeRoot: tmpDir,
      format: "yaml" as never,
    });

    expect(result.status).toBe("failed");
    expect(result.error).toContain("Unsupported export format");
  });
});

// ---------------------------------------------------------------------------
// Result shape
// ---------------------------------------------------------------------------

describe("SimulationExportResult shape", () => {
  it("has all required fields", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);
    await engine.run({ description: "Shape test", saveAs: "shape_exp" });

    const result: SimulationExportResult = exportSimulation({
      id: "shape_exp", knowledgeRoot: tmpDir, format: "json",
    });

    expect(result).toHaveProperty("status");
    expect(result).toHaveProperty("format");
    expect(result).toHaveProperty("outputPath");
    expect(typeof result.format).toBe("string");
  });
});

describe("simulate export CLI integration", () => {
  it("fails clearly for unsupported --format values", async () => {
    const engine = new SimulationEngine(mockProvider(), tmpDir);
    await engine.run({ description: "CLI bad format", saveAs: "cli_bad_fmt" });

    const result = spawnSync("npx", ["tsx", CLI, "simulate", "--export", "cli_bad_fmt", "--format", "yaml"], {
      cwd: tmpDir,
      encoding: "utf-8",
      env: buildEnv({ AUTOCONTEXT_KNOWLEDGE_ROOT: tmpDir }),
      timeout: 15000,
    });

    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Unsupported export format 'yaml'");
  });
});
