/**
 * Tests for AC-430 Phase 3: Model browsing and resolution.
 *
 * - Known models per provider
 * - Model resolution priority chain
 * - Auth-aware model listing
 * - CLI commands: `autoctx models`, `autoctx providers`
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-models-"));
}

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

const SANITIZED_KEYS = [
  "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AUTOCONTEXT_API_KEY",
  "AUTOCONTEXT_AGENT_API_KEY", "AUTOCONTEXT_PROVIDER", "AUTOCONTEXT_AGENT_PROVIDER",
  "AUTOCONTEXT_DB_PATH", "AUTOCONTEXT_RUNS_ROOT", "AUTOCONTEXT_KNOWLEDGE_ROOT",
  "AUTOCONTEXT_CONFIG_DIR", "AUTOCONTEXT_AGENT_DEFAULT_MODEL", "AUTOCONTEXT_MODEL",
  "GEMINI_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
  "AZURE_OPENAI_API_KEY",
];

function buildEnv(overrides: Record<string, string> = {}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, NODE_NO_WARNINGS: "1" };
  for (const k of SANITIZED_KEYS) delete env[k];
  return { ...env, ...overrides };
}

function runCli(
  args: string[],
  opts: { cwd?: string; env?: Record<string, string> } = {},
): { stdout: string; stderr: string; exitCode: number } {
  const r = spawnSync("npx", ["tsx", CLI, ...args], {
    encoding: "utf8",
    timeout: 15000,
    cwd: opts.cwd,
    env: buildEnv(opts.env),
  });
  return { stdout: r.stdout ?? "", stderr: r.stderr ?? "", exitCode: r.status ?? 1 };
}

// ---------------------------------------------------------------------------
// Known models per provider
// ---------------------------------------------------------------------------

describe("Known models registry", () => {
  it("exports PROVIDER_MODELS with entries for major providers", async () => {
    const { PROVIDER_MODELS } = await import("../src/config/credentials.js");
    expect(PROVIDER_MODELS.anthropic.length).toBeGreaterThan(0);
    expect(PROVIDER_MODELS.openai.length).toBeGreaterThan(0);
    expect(PROVIDER_MODELS.gemini.length).toBeGreaterThan(0);
  });

  it("each model entry has id and displayName", async () => {
    const { PROVIDER_MODELS } = await import("../src/config/credentials.js");
    for (const [, models] of Object.entries(PROVIDER_MODELS)) {
      for (const m of models) {
        expect(typeof m.id).toBe("string");
        expect(m.id.length).toBeGreaterThan(0);
        expect(typeof m.displayName).toBe("string");
      }
    }
  });

  it("getModelsForProvider returns models for known provider", async () => {
    const { getModelsForProvider } = await import("../src/config/credentials.js");
    const models = getModelsForProvider("anthropic");
    expect(models.length).toBeGreaterThan(0);
    expect(models.some((m) => m.id.includes("claude"))).toBe(true);
  });

  it("getModelsForProvider returns empty array for unknown provider", async () => {
    const { getModelsForProvider } = await import("../src/config/credentials.js");
    expect(getModelsForProvider("nonexistent")).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Model resolution priority
// ---------------------------------------------------------------------------

describe("resolveModel", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("CLI flag takes highest precedence", async () => {
    const { resolveModel, saveProviderCredentials } = await import("../src/config/credentials.js");
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-123", model: "stored-model" });
    const model = resolveModel({
      cliModel: "cli-model",
      configDir: dir,
      provider: "anthropic",
    });
    expect(model).toBe("cli-model");
  });

  it("project config model is second priority", async () => {
    const { resolveModel } = await import("../src/config/credentials.js");
    const model = resolveModel({
      projectModel: "project-model",
      configDir: dir,
      provider: "anthropic",
    });
    expect(model).toBe("project-model");
  });

  it("stored credential model is third priority", async () => {
    const { resolveModel, saveProviderCredentials } = await import("../src/config/credentials.js");
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-123", model: "stored-model" });
    const model = resolveModel({
      configDir: dir,
      provider: "anthropic",
    });
    expect(model).toBe("stored-model");
  });

  it("falls back to first known model for provider", async () => {
    const { resolveModel } = await import("../src/config/credentials.js");
    const model = resolveModel({
      configDir: dir,
      provider: "anthropic",
    });
    expect(model).toBeDefined();
    expect(model!.includes("claude")).toBe(true);
  });

  it("returns undefined for unknown provider with no stored model", async () => {
    const { resolveModel } = await import("../src/config/credentials.js");
    const model = resolveModel({
      configDir: dir,
      provider: "custom-unknown",
    });
    expect(model).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// listAuthenticatedModels — auth-aware model listing
// ---------------------------------------------------------------------------

describe("listAuthenticatedModels", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("returns models only for authenticated providers", async () => {
    const { listAuthenticatedModels, saveProviderCredentials } = await import("../src/config/credentials.js");
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-123" });
    // openai not configured

    const models = listAuthenticatedModels(dir);
    expect(models.some((m) => m.provider === "anthropic")).toBe(true);
    expect(models.every((m) => m.provider !== "openai")).toBe(true);
  });

  it("includes models from env-var authenticated providers", async () => {
    const { listAuthenticatedModels } = await import("../src/config/credentials.js");

    const oldKey = process.env.OPENAI_API_KEY;
    process.env.OPENAI_API_KEY = "sk-test-env";
    try {
      const models = listAuthenticatedModels(dir);
      expect(models.some((m) => m.provider === "openai")).toBe(true);
    } finally {
      if (oldKey === undefined) delete process.env.OPENAI_API_KEY;
      else process.env.OPENAI_API_KEY = oldKey;
    }
  });

  it("returns empty array when no providers are authenticated", async () => {
    const { listAuthenticatedModels } = await import("../src/config/credentials.js");
    const models = listAuthenticatedModels(dir);
    expect(models).toEqual([]);
  });

  it("each entry has provider, modelId, and displayName", async () => {
    const { listAuthenticatedModels, saveProviderCredentials } = await import("../src/config/credentials.js");
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-123" });

    const models = listAuthenticatedModels(dir);
    for (const m of models) {
      expect(typeof m.provider).toBe("string");
      expect(typeof m.modelId).toBe("string");
      expect(typeof m.displayName).toBe("string");
    }
  });
});

// ---------------------------------------------------------------------------
// CLI: autoctx providers
// ---------------------------------------------------------------------------

describe("autoctx providers", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("lists all known providers as JSON", () => {
    const { stdout, exitCode } = runCli(["providers"], {
      cwd: dir,
      env: { AUTOCONTEXT_CONFIG_DIR: join(dir, "config") },
    });
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout) as Array<Record<string, unknown>>;
    expect(Array.isArray(parsed)).toBe(true);
    expect(parsed.some((p) => p.id === "anthropic")).toBe(true);
    expect(parsed.some((p) => p.id === "gemini")).toBe(true);
  });

  it("shows authenticated status for configured providers", async () => {
    const configDir = join(dir, "config");
    const { saveProviderCredentials } = await import("../src/config/credentials.js");
    saveProviderCredentials(configDir, "anthropic", { apiKey: "sk-ant-123" });

    const { stdout, exitCode } = runCli(["providers"], {
      cwd: dir,
      env: { AUTOCONTEXT_CONFIG_DIR: configDir },
    });
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout) as Array<Record<string, unknown>>;
    const anthropic = parsed.find((p) => p.id === "anthropic");
    expect(anthropic?.authenticated).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// CLI: autoctx models
// ---------------------------------------------------------------------------

describe("autoctx models", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("lists models for authenticated providers as JSON", async () => {
    const configDir = join(dir, "config");
    const { saveProviderCredentials } = await import("../src/config/credentials.js");
    saveProviderCredentials(configDir, "anthropic", { apiKey: "sk-ant-123" });

    const { stdout, exitCode } = runCli(["models"], {
      cwd: dir,
      env: { AUTOCONTEXT_CONFIG_DIR: configDir },
    });
    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout) as Array<Record<string, unknown>>;
    expect(Array.isArray(parsed)).toBe(true);
    expect(parsed.length).toBeGreaterThan(0);
    expect(parsed.every((m) => m.provider === "anthropic")).toBe(true);
  });

  it("shows hint when no providers are authenticated", () => {
    const { stdout, exitCode } = runCli(["models"], {
      cwd: dir,
      env: { AUTOCONTEXT_CONFIG_DIR: join(dir, "config") },
    });
    expect(exitCode).toBe(0);
    expect(stdout).toContain("autoctx login");
  });
});
