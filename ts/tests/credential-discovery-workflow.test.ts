import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  discoverAllProviders,
  getKnownProvider,
  KNOWN_PROVIDERS,
  type DiscoveredProvider,
  type KnownProvider,
} from "../src/config/credential-provider-discovery.js";
import { saveProviderCredentials } from "../src/config/credential-store.js";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-credential-discovery-"));
}

describe("credential provider discovery workflow", () => {
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

  it("exposes provider metadata and discovers stored providers before env providers", () => {
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-stored" });
    process.env.ANTHROPIC_API_KEY = "sk-ant-env";
    process.env.OPENAI_API_KEY = "sk-openai-env";

    expect(KNOWN_PROVIDERS.some((provider: KnownProvider) => provider.id === "anthropic")).toBe(true);
    expect(getKnownProvider("anthropic")).toMatchObject({ displayName: "Anthropic" });

    const discovered = discoverAllProviders(dir);
    expect(discovered.find((provider: DiscoveredProvider) => provider.provider === "anthropic")).toMatchObject({
      source: "stored",
      hasApiKey: true,
    });
    expect(discovered.find((provider: DiscoveredProvider) => provider.provider === "openai")).toMatchObject({
      source: "env",
      hasApiKey: true,
    });
  });

  it("discovers generic AUTOCONTEXT_* provider settings", () => {
    process.env.AUTOCONTEXT_AGENT_PROVIDER = "openai";
    process.env.AUTOCONTEXT_AGENT_API_KEY = "sk-generic-env";
    process.env.AUTOCONTEXT_AGENT_DEFAULT_MODEL = "gpt-5.6-luna";
    process.env.AUTOCONTEXT_AGENT_BASE_URL = "https://api.example.test/v1";

    expect(discoverAllProviders(dir)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          provider: "openai",
          source: "env",
          hasApiKey: true,
          model: "gpt-5.6-luna",
          baseUrl: "https://api.example.test/v1",
        }),
      ]),
    );
  });
});
