import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  getModelsForProvider,
  listAuthenticatedModels,
  PROVIDER_MODELS,
  resolveModel,
  type AuthenticatedModel,
  type KnownModel,
} from "../src/config/credential-model-catalog.js";
import { validateApiKey } from "../src/config/credential-validation.js";
import { saveProviderCredentials } from "../src/config/credential-store.js";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-credential-models-"));
}

describe("credential model and validation workflows", () => {
  let dir: string;
  const savedEnv = { ...process.env };

  beforeEach(() => {
    dir = makeTempDir();
    for (const key of Object.keys(process.env)) {
      if (key.startsWith("AUTOCONTEXT_") || key.endsWith("_API_KEY")) {
        delete process.env[key];
      }
    }
  });

  afterEach(() => {
    process.env = { ...savedEnv };
    rmSync(dir, { recursive: true, force: true });
  });

  it("exposes known provider models and resolves model precedence", () => {
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-123", model: "stored-model" });

    expect(PROVIDER_MODELS.anthropic.length).toBeGreaterThan(0);
    expect(getModelsForProvider("anthropic").some((model: KnownModel) => model.id.includes("claude"))).toBe(true);
    expect(resolveModel({ cliModel: "cli-model", configDir: dir, provider: "anthropic" })).toBe("cli-model");
    expect(resolveModel({ projectModel: "project-model", configDir: dir, provider: "anthropic" })).toBe("project-model");
    expect(resolveModel({ envModel: "env-model", configDir: dir, provider: "anthropic" })).toBe("env-model");
    expect(resolveModel({ configDir: dir, provider: "anthropic" })).toBe("stored-model");
  });

  it("lists authenticated models from stored and env-backed providers", () => {
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-123" });
    process.env.OPENAI_API_KEY = "sk-openai-env";

    const authenticated = listAuthenticatedModels(dir);
    expect(authenticated.some((model: AuthenticatedModel) => model.provider === "anthropic")).toBe(true);
    expect(authenticated.some((model: AuthenticatedModel) => model.provider === "openai")).toBe(true);
  });

  it("uses explicit provider defaults instead of premium catalog ordering", () => {
    expect(resolveModel({ configDir: dir, provider: "anthropic" })).toBe("claude-sonnet-5");
    expect(resolveModel({ configDir: dir, provider: "openai" })).toBe("gpt-5.6-terra");
    expect(resolveModel({ configDir: dir, provider: "openrouter" })).toBe(
      "anthropic/claude-sonnet-5",
    );
    expect(resolveModel({ configDir: dir, provider: "unknown" })).toBeUndefined();
  });

  it("validates provider api keys using provider-specific rules", async () => {
    await expect(validateApiKey("anthropic", "sk-ant-valid")).resolves.toEqual({ valid: true });
    await expect(validateApiKey("groq", "bad-key")).resolves.toMatchObject({ valid: false });
    await expect(validateApiKey("ollama", "")).resolves.toEqual({ valid: true });
  });
});
