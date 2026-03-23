/**
 * Tests for AC-367: Non-Pi provider, runtime, and config-routing parity.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-provider-routing-"));
}

const savedEnv: Record<string, string | undefined> = {};

function restoreProviderEnv(): void {
  for (const key of Object.keys(process.env)) {
    if (key.startsWith("AUTOCONTEXT_") || key === "ANTHROPIC_API_KEY" || key === "OPENAI_API_KEY") {
      if (key in savedEnv) {
        process.env[key] = savedEnv[key];
      } else {
        delete process.env[key];
      }
    }
  }
}

function saveAndClearProviderEnv(): void {
  for (const key of Object.keys(process.env)) {
    if (key.startsWith("AUTOCONTEXT_") || key === "ANTHROPIC_API_KEY" || key === "OPENAI_API_KEY") {
      savedEnv[key] = process.env[key];
      delete process.env[key];
    }
  }
}

// ---------------------------------------------------------------------------
// createProvider factory completeness
// ---------------------------------------------------------------------------

describe("createProvider factory", () => {
  it("supports hermes provider type", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    const provider = createProvider({ providerType: "hermes" });
    expect(provider.name).toBe("hermes-gateway");
    expect(provider.defaultModel()).toContain("hermes");
  });

  it("supports hermes with custom base_url and model", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    const provider = createProvider({
      providerType: "hermes",
      baseUrl: "http://hermes.local:8080/v1",
      model: "hermes-3-llama-3.1-70b",
    });
    expect(provider.defaultModel()).toBe("hermes-3-llama-3.1-70b");
  });

  it("error message lists all supported providers including hermes", async () => {
    const { createProvider } = await import("../src/providers/index.js");
    try {
      createProvider({ providerType: "nonexistent" });
    } catch (err) {
      const msg = (err as Error).message;
      expect(msg).toContain("hermes");
      expect(msg).toContain("anthropic");
      expect(msg).toContain("deterministic");
    }
  });
});

// ---------------------------------------------------------------------------
// resolveProviderConfig env var alignment
// ---------------------------------------------------------------------------

describe("resolveProviderConfig env var alignment", () => {
  afterEach(() => {
    restoreProviderEnv();
  });

  it("reads AUTOCONTEXT_AGENT_PROVIDER (Python-compatible)", async () => {
    saveAndClearProviderEnv();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "deterministic";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.providerType).toBe("deterministic");
  });

  it("falls back to AUTOCONTEXT_PROVIDER for backward compat", async () => {
    saveAndClearProviderEnv();
    process.env.AUTOCONTEXT_PROVIDER = "deterministic";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.providerType).toBe("deterministic");
  });

  it("AUTOCONTEXT_AGENT_PROVIDER takes precedence over AUTOCONTEXT_PROVIDER", async () => {
    saveAndClearProviderEnv();
    process.env.AUTOCONTEXT_PROVIDER = "anthropic";
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "deterministic";
    process.env.ANTHROPIC_API_KEY = "sk-test";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.providerType).toBe("deterministic");
  });

  it("reads AUTOCONTEXT_AGENT_BASE_URL", async () => {
    saveAndClearProviderEnv();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "openai-compatible";
    process.env.AUTOCONTEXT_AGENT_API_KEY = "test-key";
    process.env.AUTOCONTEXT_AGENT_BASE_URL = "http://custom:8080/v1";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.baseUrl).toBe("http://custom:8080/v1");
  });

  it("reads AUTOCONTEXT_AGENT_DEFAULT_MODEL", async () => {
    saveAndClearProviderEnv();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "openai-compatible";
    process.env.AUTOCONTEXT_AGENT_API_KEY = "test-key";
    process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL = "gpt-4o-mini";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.model).toBe("gpt-4o-mini");
  });

  it("resolves hermes provider from env vars", async () => {
    saveAndClearProviderEnv();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "hermes";
    const { resolveProviderConfig } = await import("../src/providers/index.js");
    const config = resolveProviderConfig();
    expect(config.providerType).toBe("hermes");
  });
});

// ---------------------------------------------------------------------------
// Per-role provider support
// ---------------------------------------------------------------------------

describe("Per-role provider configuration", () => {
  afterEach(() => {
    restoreProviderEnv();
  });

  it("loadSettings reads AUTOCONTEXT_COMPETITOR_PROVIDER", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({
      competitorProvider: "ollama",
    });
    expect(settings.competitorProvider).toBe("ollama");
  });

  it("loadSettings reads AUTOCONTEXT_ANALYST_PROVIDER", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({
      analystProvider: "vllm",
    });
    expect(settings.analystProvider).toBe("vllm");
  });

  it("per-role provider defaults to empty (use agent_provider)", async () => {
    const { AppSettingsSchema } = await import("../src/config/index.js");
    const settings = AppSettingsSchema.parse({});
    expect(settings.competitorProvider).toBe("");
    expect(settings.analystProvider).toBe("");
    expect(settings.coachProvider).toBe("");
    expect(settings.architectProvider).toBe("");
  });

  it("buildRoleProviderBundle applies AUTOCONTEXT_AGENT_* defaults and per-role overrides", async () => {
    saveAndClearProviderEnv();
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "hermes";
    process.env.AUTOCONTEXT_AGENT_API_KEY = "hermes-key";
    process.env.AUTOCONTEXT_AGENT_BASE_URL = "http://hermes.local:8080/v1";
    process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL = "hermes-default";
    process.env.AUTOCONTEXT_COMPETITOR_PROVIDER = "deterministic";
    process.env.AUTOCONTEXT_MODEL_ANALYST = "analyst-model";
    process.env.AUTOCONTEXT_MODEL_COACH = "coach-model";

    const { buildRoleProviderBundle } = await import("../src/providers/index.js");
    const { loadSettings } = await import("../src/config/index.js");
    const bundle = buildRoleProviderBundle(loadSettings());

    expect(bundle.defaultProvider.name).toBe("hermes-gateway");
    expect(bundle.defaultConfig.baseUrl).toBe("http://hermes.local:8080/v1");
    expect(bundle.defaultConfig.apiKey).toBe("hermes-key");
    expect(bundle.roleProviders.competitor?.name).toBe("deterministic");
    expect(bundle.roleModels.analyst).toBe("analyst-model");
    expect(bundle.roleModels.coach).toBe("coach-model");
  });
});

describe("Live provider routing", () => {
  let dir: string;

  beforeEach(() => {
    dir = makeTempDir();
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("GenerationRunner uses per-role providers and models in the live loop", async () => {
    const { GenerationRunner } = await import("../src/loop/generation-runner.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    const dbPath = join(dir, "routing.db");
    const store = new SQLiteStore(dbPath);
    store.migrate(join(__dirname, "..", "migrations"));

    const calls: Array<{ role: string; model?: string }> = [];
    let defaultCalls = 0;

    const defaultProvider = {
      name: "default",
      defaultModel: () => "default-model",
      complete: async () => {
        defaultCalls++;
        return { text: "{}", usage: {}, model: "default-model" };
      },
    };
    const competitorProvider = {
      name: "competitor",
      defaultModel: () => "competitor-default",
      complete: async (opts: { model?: string }) => {
        calls.push({ role: "competitor", model: opts.model });
        return {
          text: '{"aggression":0.8,"defense":0.2,"path_bias":0.4}',
          usage: {},
          model: opts.model,
        };
      },
    };
    const analystProvider = {
      name: "analyst",
      defaultModel: () => "analyst-default",
      complete: async (opts: { model?: string }) => {
        calls.push({ role: "analyst", model: opts.model });
        return {
          text: "## Findings\n- Stable opening\n## Root Causes\n- Good lane coverage\n## Actionable Recommendations\n- Preserve the center push",
          usage: {},
          model: opts.model,
        };
      },
    };
    const coachProvider = {
      name: "coach",
      defaultModel: () => "coach-default",
      complete: async (opts: { model?: string }) => {
        calls.push({ role: "coach", model: opts.model });
        return {
          text: [
            "<!-- PLAYBOOK_START -->",
            "Keep balanced pressure.",
            "<!-- PLAYBOOK_END -->",
            "<!-- LESSONS_START -->",
            "- Center pressure improved win rate.",
            "<!-- LESSONS_END -->",
            "<!-- COMPETITOR_HINTS_START -->",
            "- Avoid abandoning defense.",
            "<!-- COMPETITOR_HINTS_END -->",
          ].join("\n"),
          usage: {},
          model: opts.model,
        };
      },
    };

    const runner = new GenerationRunner({
      provider: defaultProvider as any,
      roleProviders: {
        competitor: competitorProvider as any,
        analyst: analystProvider as any,
        coach: coachProvider as any,
      },
      roleModels: {
        competitor: "competitor-model",
        analyst: "analyst-model",
        coach: "coach-model",
      },
      scenario: new GridCtfScenario(),
      store,
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      matchesPerGeneration: 1,
      maxRetries: 0,
      minDelta: 0,
    });

    const result = await runner.run("routing-run", 1);
    expect(result.generationsCompleted).toBe(1);
    expect(defaultCalls).toBe(0);
    expect(calls).toEqual([
      { role: "competitor", model: "competitor-model" },
      { role: "analyst", model: "analyst-model" },
      { role: "coach", model: "coach-model" },
    ]);

    const outputs = store.getAgentOutputs("routing-run", 1);
    expect(JSON.parse(outputs.find((row) => row.role === "competitor")?.content ?? "{}")).toMatchObject({
      aggression: 0.8,
      defense: 0.2,
      path_bias: 0.4,
    });
    expect(outputs.find((row) => row.role === "analyst")?.content).toContain("Stable opening");
    expect(outputs.find((row) => row.role === "coach")?.content).toContain("PLAYBOOK_START");

    store.close();
  });
});
