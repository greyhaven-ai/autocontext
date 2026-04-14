/**
 * Tests for AC-522: Provider surface consistency.
 *
 * KNOWN_PROVIDERS (credentials.ts), createProvider() factory,
 * and README must all agree on which providers exist.
 */

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const EXPECTED_PROVIDER_IDS = [
  "anthropic",
  "openai",
  "openai-compatible",
  "gemini",
  "mistral",
  "groq",
  "openrouter",
  "azure-openai",
  "ollama",
  "vllm",
  "hermes",
  "claude-cli",
  "codex",
  "pi",
  "pi-rpc",
  "deterministic",
] as const;

function readSupportedProvidersFromReadme(): string[] {
  const readme = readFileSync(new URL("../README.md", import.meta.url), "utf8");
  const match = readme.match(/^Supported providers:\s+(.+)$/m);
  expect(match, "README missing supported providers line").not.toBeNull();
  return [...match![1].matchAll(/`([^`]+)`/g)].map((entry) => entry[1]);
}

describe("Provider surface consistency", () => {
  it("KNOWN_PROVIDERS includes all createProvider factory types", async () => {
    const { KNOWN_PROVIDERS } = await import("../src/config/credentials.js");
    const knownIds = new Set(KNOWN_PROVIDERS.map((p: { id: string }) => p.id));

    for (const type of EXPECTED_PROVIDER_IDS) {
      expect(knownIds.has(type), `KNOWN_PROVIDERS missing factory type: ${type}`).toBe(true);
    }
  });

  it("createProvider() handles all KNOWN_PROVIDERS ids", async () => {
    const { KNOWN_PROVIDERS } = await import("../src/config/credentials.js");
    const { createProvider } = await import("../src/providers/index.js");

    // Every KNOWN_PROVIDER id should be accepted by createProvider without throwing "Unknown provider"
    // We can't fully construct all (missing API keys), but the factory should recognize the type
    const knownIds = KNOWN_PROVIDERS.map((p: { id: string }) => p.id);

    for (const id of knownIds) {
      // For key-requiring providers, createProvider may throw on missing key,
      // but should NOT throw "Unknown provider type"
      try {
        createProvider({ providerType: id });
      } catch (e: any) {
        expect(e.message).not.toContain("Unknown provider type");
      }
    }
  });

  it("new compat providers use provider-specific default models", async () => {
    const { createProvider } = await import("../src/providers/index.js");

    expect(createProvider({ providerType: "gemini", apiKey: "gem-key" }).defaultModel()).toBe(
      "gemini-2.5-pro",
    );
    expect(createProvider({ providerType: "mistral", apiKey: "mistral-key" }).defaultModel()).toBe(
      "mistral-large-latest",
    );
    expect(createProvider({ providerType: "groq", apiKey: "groq-key" }).defaultModel()).toBe(
      "llama-3.3-70b-versatile",
    );
    expect(
      createProvider({ providerType: "openrouter", apiKey: "openrouter-key" }).defaultModel(),
    ).toBe("anthropic/claude-sonnet-4");
    expect(
      createProvider({
        providerType: "azure-openai",
        apiKey: "azure-key",
        baseUrl: "https://azure.example.com/openai/v1",
      }).defaultModel(),
    ).toBe("gpt-4o");
  });

  it("KNOWN_PROVIDERS has entries for subscription-backed CLI runtimes and gateway providers", async () => {
    const { KNOWN_PROVIDERS } = await import("../src/config/credentials.js");
    const ids = KNOWN_PROVIDERS.map((p: { id: string }) => p.id);

    expect(ids).toContain("hermes");
    expect(ids).toContain("claude-cli");
    expect(ids).toContain("codex");
    expect(ids).toContain("pi");
    expect(ids).toContain("pi-rpc");
  });

  it("KNOWN_PROVIDERS has all expected provider entries", async () => {
    const { KNOWN_PROVIDERS } = await import("../src/config/credentials.js");
    const ids = KNOWN_PROVIDERS.map((p: { id: string }) => p.id).sort();
    expect(ids).toEqual([...EXPECTED_PROVIDER_IDS].sort());
  });

  it("README supported providers line matches the runtime provider surface", () => {
    const readmeIds = readSupportedProvidersFromReadme().sort();
    expect(readmeIds).toEqual([...EXPECTED_PROVIDER_IDS].sort());
  });
});
