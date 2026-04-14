/**
 * Tests for AC-342 Task 1: Config/Settings — Full AppSettings Zod schema
 * with AUTOCONTEXT_* env var loading and preset support.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

describe("AppSettingsSchema", () => {
  it("should export a Zod schema", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    expect(AppSettingsSchema).toBeDefined();
    expect(typeof AppSettingsSchema.parse).toBe("function");
  });

  it("should parse with all defaults when given empty object", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({});
    expect(settings.agentProvider).toBe("anthropic");
    expect(settings.executorMode).toBe("local");
    expect(settings.matchesPerGeneration).toBe(3);
    expect(settings.defaultGenerations).toBe(1);
    expect(settings.maxRetries).toBe(2);
    expect(settings.seedBase).toBe(1000);
  });

  it("should include path defaults", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({});
    expect(settings.dbPath).toBe("runs/autocontext.sqlite3");
    expect(settings.runsRoot).toBe("runs");
    expect(settings.knowledgeRoot).toBe("knowledge");
    expect(settings.skillsRoot).toBe("skills");
    expect(settings.eventStreamPath).toBe("runs/events.ndjson");
  });

  it("should include model defaults", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({});
    expect(settings.modelCompetitor).toContain("sonnet");
    expect(settings.modelAnalyst).toContain("sonnet");
    expect(settings.modelCoach).toContain("opus");
    expect(settings.modelArchitect).toContain("opus");
  });

  it("should include OpenClaw runtime defaults", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({});
    expect(settings.openclawRuntimeKind).toBe("factory");
    expect(settings.openclawAgentFactory).toBe("");
    expect(settings.openclawAgentCommand).toBe("");
    expect(settings.openclawAgentHttpEndpoint).toBe("");
    expect(settings.openclawCompatibilityVersion).toBe("1.0");
    expect(settings.openclawTimeoutSeconds).toBe(30.0);
    expect(settings.openclawMaxRetries).toBe(2);
    expect(settings.openclawRetryBaseDelay).toBe(0.25);
    expect(settings.openclawDistillSidecarFactory).toBe("");
    expect(settings.openclawDistillSidecarCommand).toBe("");
  });

  it("should include judge defaults", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({});
    expect(settings.judgeModel).toContain("sonnet");
    expect(settings.judgeSamples).toBe(1);
    expect(settings.judgeTemperature).toBe(0.0);
    expect(settings.judgeProvider).toBe("anthropic");
  });

  it("should include boolean feature flag defaults", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({});
    expect(settings.curatorEnabled).toBe(true);
    expect(settings.crossRunInheritance).toBe(true);
    expect(settings.rlmEnabled).toBe(false);
    expect(settings.codeStrategiesEnabled).toBe(false);
    expect(settings.noveltyEnabled).toBe(true);
    expect(settings.holdoutEnabled).toBe(true);
    expect(settings.costTrackingEnabled).toBe(true);
  });

  it("should accept overrides", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({
      agentProvider: "deterministic",
      matchesPerGeneration: 5,
      maxRetries: 0,
      curatorEnabled: false,
    });
    expect(settings.agentProvider).toBe("deterministic");
    expect(settings.matchesPerGeneration).toBe(5);
    expect(settings.maxRetries).toBe(0);
    expect(settings.curatorEnabled).toBe(false);
  });

  it("should validate numeric constraints", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    expect(() =>
      AppSettingsSchema.parse({ matchesPerGeneration: 0 }),
    ).toThrow();
    expect(() =>
      AppSettingsSchema.parse({ maxRetries: -1 }),
    ).toThrow();
  });

  it("should coerce cost_budget_limit of 0 to null", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({ costBudgetLimit: 0 });
    expect(settings.costBudgetLimit).toBeNull();
  });

  it("should keep non-zero cost_budget_limit", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({ costBudgetLimit: 50.0 });
    expect(settings.costBudgetLimit).toBe(50.0);
  });

  it("should include exploration settings", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({});
    expect(settings.explorationMode).toBe("linear");
    expect(settings.noveltyWeight).toBe(0.1);
    expect(settings.divergentCompetitorEnabled).toBe(true);
    expect(settings.multiBasinEnabled).toBe(false);
  });

  it("should include backpressure settings", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({});
    expect(settings.backpressureMinDelta).toBe(0.005);
    expect(settings.backpressureMode).toBe("simple");
    expect(settings.backpressurePlateauWindow).toBe(3);
  });
});

describe("loadSettings", () => {
  const savedEnv: Record<string, string | undefined> = {};

  beforeEach(() => {
    // Save and clear all AUTOCONTEXT_ env vars
    for (const key of Object.keys(process.env)) {
      if (key.startsWith("AUTOCONTEXT_")) {
        savedEnv[key] = process.env[key];
        delete process.env[key];
      }
    }
  });

  afterEach(() => {
    // Restore
    for (const key of Object.keys(process.env)) {
      if (key.startsWith("AUTOCONTEXT_")) {
        delete process.env[key];
      }
    }
    for (const [key, val] of Object.entries(savedEnv)) {
      if (val !== undefined) process.env[key] = val;
    }
  });

  it("should load defaults with no env vars", async () => {
    const { loadSettings } = await import("../src/config/index.js");
    const settings = loadSettings();
    expect(settings.agentProvider).toBe("anthropic");
    expect(settings.matchesPerGeneration).toBe(3);
  });

  it("should read AUTOCONTEXT_AGENT_PROVIDER", async () => {
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "deterministic";
    const { loadSettings } = await import("../src/config/index.js");
    const settings = loadSettings();
    expect(settings.agentProvider).toBe("deterministic");
  });

  it("should coerce string to number for AUTOCONTEXT_MATCHES_PER_GENERATION", async () => {
    process.env.AUTOCONTEXT_MATCHES_PER_GENERATION = "7";
    const { loadSettings } = await import("../src/config/index.js");
    const settings = loadSettings();
    expect(settings.matchesPerGeneration).toBe(7);
  });

  it("should coerce string to boolean for AUTOCONTEXT_CURATOR_ENABLED", async () => {
    process.env.AUTOCONTEXT_CURATOR_ENABLED = "false";
    const { loadSettings } = await import("../src/config/index.js");
    const settings = loadSettings();
    expect(settings.curatorEnabled).toBe(false);
  });

  it("should read AUTOCONTEXT_ANTHROPIC_API_KEY", async () => {
    process.env.AUTOCONTEXT_ANTHROPIC_API_KEY = "sk-test-123";
    const { loadSettings } = await import("../src/config/index.js");
    const settings = loadSettings();
    expect(settings.anthropicApiKey).toBe("sk-test-123");
  });

  it("should read ANTHROPIC_API_KEY as a standard alias", async () => {
    process.env.ANTHROPIC_API_KEY = "sk-standard-123";
    const { loadSettings } = await import("../src/config/index.js");
    const settings = loadSettings();
    expect(settings.anthropicApiKey).toBe("sk-standard-123");
  });

  it("should read AUTOCONTEXT_OPENCLAW_* env vars", async () => {
    process.env.AUTOCONTEXT_OPENCLAW_RUNTIME_KIND = "http";
    process.env.AUTOCONTEXT_OPENCLAW_AGENT_HTTP_ENDPOINT = "http://127.0.0.1:8001/run";
    process.env.AUTOCONTEXT_OPENCLAW_AGENT_HTTP_HEADERS = '{"Authorization":"Bearer token"}';
    process.env.AUTOCONTEXT_OPENCLAW_TIMEOUT_SECONDS = "45";
    process.env.AUTOCONTEXT_OPENCLAW_MAX_RETRIES = "4";
    process.env.AUTOCONTEXT_OPENCLAW_RETRY_BASE_DELAY = "0.5";
    process.env.AUTOCONTEXT_OPENCLAW_DISTILL_SIDECAR_COMMAND = "python sidecar.py";

    const { loadSettings } = await import("../src/config/index.js");
    const settings = loadSettings();
    expect(settings.openclawRuntimeKind).toBe("http");
    expect(settings.openclawAgentHttpEndpoint).toBe("http://127.0.0.1:8001/run");
    expect(settings.openclawAgentHttpHeaders).toBe('{"Authorization":"Bearer token"}');
    expect(settings.openclawTimeoutSeconds).toBe(45);
    expect(settings.openclawMaxRetries).toBe(4);
    expect(settings.openclawRetryBaseDelay).toBe(0.5);
    expect(settings.openclawDistillSidecarCommand).toBe("python sidecar.py");
  });
});

describe("presets", () => {
  it("should export PRESETS map", async () => {
    const { PRESETS } = await import("../src/config/index.js");
    expect(PRESETS).toBeDefined();
    expect(PRESETS.has("quick")).toBe(true);
    expect(PRESETS.has("standard")).toBe(true);
    expect(PRESETS.has("deep")).toBe(true);
    expect(PRESETS.has("rapid")).toBe(true);
    expect(PRESETS.has("long_run")).toBe(true);
    expect(PRESETS.has("short_run")).toBe(true);
  });

  it("should apply preset overrides via applyPreset", async () => {
    const { applyPreset } = await import("../src/config/index.js");
    const overrides = applyPreset("quick");
    expect(overrides.matchesPerGeneration).toBe(2);
    expect(overrides.curatorEnabled).toBe(false);
    expect(overrides.maxRetries).toBe(0);
  });

  it("should return empty overrides for empty name", async () => {
    const { applyPreset } = await import("../src/config/index.js");
    const overrides = applyPreset("");
    expect(Object.keys(overrides).length).toBe(0);
  });

  it("should throw for unknown preset name", async () => {
    const { applyPreset } = await import("../src/config/index.js");
    expect(() => applyPreset("nonexistent")).toThrow();
  });

  it("long_run preset enables safeguards", async () => {
    const { applyPreset } = await import("../src/config/index.js");
    const overrides = applyPreset("long_run");
    expect(overrides.stagnationResetEnabled).toBe(true);
    expect(overrides.deadEndTrackingEnabled).toBe(true);
    expect(overrides.curatorEnabled).toBe(true);
  });
});

describe("AppSettings type", () => {
  it("should export AppSettings type that matches parsed schema", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    type AppSettings = ReturnType<typeof AppSettingsSchema.parse>;
    const settings: AppSettings = AppSettingsSchema.parse({});
    // TypeScript compile-time check — if this compiles, the type exists
    expect(settings.agentProvider).toBeDefined();
  });
});

describe("project config integration", () => {
  const savedEnv: Record<string, string | undefined> = {};
  let originalCwd = process.cwd();
  let dir = "";

  beforeEach(() => {
    originalCwd = process.cwd();
    dir = mkdtempSync(join(tmpdir(), "ac-config-project-"));

    for (const key of Object.keys(process.env)) {
      if (
        key.startsWith("AUTOCONTEXT_")
        || key === "ANTHROPIC_API_KEY"
        || key === "OPENAI_API_KEY"
      ) {
        savedEnv[key] = process.env[key];
        delete process.env[key];
      }
    }
  });

  afterEach(() => {
    process.chdir(originalCwd);
    rmSync(dir, { recursive: true, force: true });

    for (const key of Object.keys(process.env)) {
      if (
        key.startsWith("AUTOCONTEXT_")
        || key === "ANTHROPIC_API_KEY"
        || key === "OPENAI_API_KEY"
      ) {
        delete process.env[key];
      }
    }
    for (const [key, value] of Object.entries(savedEnv)) {
      if (value !== undefined) {
        process.env[key] = value;
      }
    }
  });

  it("loads project defaults from parent directories and resolves relative paths from project root", async () => {
    writeFileSync(join(dir, ".autoctx.json"), JSON.stringify({
      default_scenario: "grid_ctf",
      provider: "ollama",
      model: "llama3.2",
      gens: 4,
      runs_dir: "state/runs",
      knowledge_dir: "state/knowledge",
    }, null, 2), "utf-8");
    mkdirSync(join(dir, "nested", "deeper"), { recursive: true });
    process.chdir(join(dir, "nested", "deeper"));

    const { loadProjectConfig, loadSettings } = await import("../src/config/index.js");

    const projectConfig = loadProjectConfig();
    expect(projectConfig?.defaultScenario).toBe("grid_ctf");
    expect(projectConfig?.runsDir?.endsWith(join("state", "runs"))).toBe(true);
    expect(projectConfig?.knowledgeDir?.endsWith(join("state", "knowledge"))).toBe(true);

    const settings = loadSettings();
    expect(settings.agentProvider).toBe("ollama");
    expect(settings.modelCompetitor).toBe("llama3.2");
    expect(settings.modelAnalyst).toBe("llama3.2");
    expect(settings.defaultGenerations).toBe(4);
    expect(settings.runsRoot.endsWith(join("state", "runs"))).toBe(true);
    expect(settings.knowledgeRoot.endsWith(join("state", "knowledge"))).toBe(true);
    expect(settings.dbPath.endsWith(join("state", "runs", "autocontext.sqlite3"))).toBe(true);
  });
});
