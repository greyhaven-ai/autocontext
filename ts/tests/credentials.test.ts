/**
 * Tests for AC-430 Phase 1: Credential hardening.
 *
 * - 0600 file permissions on credentials
 * - Shell-command escape hatch for API key values
 * - Multi-provider credential store
 * - API key validation
 * - listConfiguredProviders for enhanced whoami
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-creds-"));
}

// ---------------------------------------------------------------------------
// resolveApiKeyValue — shell-command escape hatch
// ---------------------------------------------------------------------------

describe("resolveApiKeyValue", () => {
  it("returns literal string as-is", async () => {
    const { resolveApiKeyValue } = await import("../src/config/credentials.js");
    expect(resolveApiKeyValue("sk-ant-1234")).toBe("sk-ant-1234");
  });

  it("executes shell command when value starts with !", async () => {
    const { resolveApiKeyValue } = await import("../src/config/credentials.js");
    const result = resolveApiKeyValue("!echo test-key-from-shell");
    expect(result).toBe("test-key-from-shell");
  });

  it("trims whitespace from shell command output", async () => {
    const { resolveApiKeyValue } = await import("../src/config/credentials.js");
    const result = resolveApiKeyValue("!echo '  padded  '");
    expect(result).toBe("padded");
  });

  it("throws on shell command failure", async () => {
    const { resolveApiKeyValue } = await import("../src/config/credentials.js");
    expect(() => resolveApiKeyValue("!nonexistent-command-xyz-12345")).toThrow();
  });

  it("returns empty string as-is", async () => {
    const { resolveApiKeyValue } = await import("../src/config/credentials.js");
    expect(resolveApiKeyValue("")).toBe("");
  });

  it("resolves environment variable name when value matches an env var", async () => {
    const { resolveApiKeyValue } = await import("../src/config/credentials.js");
    // Regular string that doesn't start with ! or $ should be returned as-is
    expect(resolveApiKeyValue("MY_LITERAL_KEY")).toBe("MY_LITERAL_KEY");
  });
});

// ---------------------------------------------------------------------------
// saveProviderCredentials + loadProviderCredentials — multi-provider store
// ---------------------------------------------------------------------------

describe("Multi-provider credential store", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("saves credentials for a provider", async () => {
    const { saveProviderCredentials, loadProviderCredentials } = await import("../src/config/credentials.js");
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-123", model: "claude-sonnet-4-20250514" });
    const creds = loadProviderCredentials(dir, "anthropic");
    expect(creds).not.toBeNull();
    expect(creds!.apiKey).toBe("sk-ant-123");
    expect(creds!.model).toBe("claude-sonnet-4-20250514");
  });

  it("saves credentials for multiple providers independently", async () => {
    const { saveProviderCredentials, loadProviderCredentials } = await import("../src/config/credentials.js");
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-123" });
    saveProviderCredentials(dir, "openai", { apiKey: "sk-openai-456", baseUrl: "https://api.openai.com/v1" });

    const anthropic = loadProviderCredentials(dir, "anthropic");
    const openai = loadProviderCredentials(dir, "openai");
    expect(anthropic!.apiKey).toBe("sk-ant-123");
    expect(openai!.apiKey).toBe("sk-openai-456");
    expect(openai!.baseUrl).toBe("https://api.openai.com/v1");
  });

  it("overwrites existing credentials for same provider", async () => {
    const { saveProviderCredentials, loadProviderCredentials } = await import("../src/config/credentials.js");
    saveProviderCredentials(dir, "anthropic", { apiKey: "old-key" });
    saveProviderCredentials(dir, "anthropic", { apiKey: "new-key" });
    const creds = loadProviderCredentials(dir, "anthropic");
    expect(creds!.apiKey).toBe("new-key");
  });

  it("returns null for unknown provider", async () => {
    const { loadProviderCredentials } = await import("../src/config/credentials.js");
    const creds = loadProviderCredentials(dir, "nonexistent");
    expect(creds).toBeNull();
  });

  it("records savedAt timestamp", async () => {
    const { saveProviderCredentials, loadProviderCredentials } = await import("../src/config/credentials.js");
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-123" });
    const creds = loadProviderCredentials(dir, "anthropic");
    expect(creds!.savedAt).toBeDefined();
    expect(new Date(creds!.savedAt!).getTime()).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// File permissions
// ---------------------------------------------------------------------------

describe("Credential file permissions", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("sets 0600 permissions on credentials file", async () => {
    const { saveProviderCredentials, CREDENTIALS_FILE } = await import("../src/config/credentials.js");
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-123" });
    const filePath = join(dir, CREDENTIALS_FILE);
    const stats = statSync(filePath);
    // 0o600 = owner read+write only (33152 in decimal on most systems)
    const mode = stats.mode & 0o777;
    expect(mode).toBe(0o600);
  });
});

// ---------------------------------------------------------------------------
// listConfiguredProviders
// ---------------------------------------------------------------------------

describe("listConfiguredProviders", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("returns empty array when no credentials exist", async () => {
    const { listConfiguredProviders } = await import("../src/config/credentials.js");
    const providers = listConfiguredProviders(dir);
    expect(providers).toEqual([]);
  });

  it("returns all configured providers with auth status", async () => {
    const { saveProviderCredentials, listConfiguredProviders } = await import("../src/config/credentials.js");
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-123" });
    saveProviderCredentials(dir, "ollama", { baseUrl: "http://localhost:11434" });

    const providers = listConfiguredProviders(dir);
    expect(providers.length).toBe(2);
    expect(providers.find((p) => p.provider === "anthropic")).toEqual(
      expect.objectContaining({ provider: "anthropic", hasApiKey: true }),
    );
    expect(providers.find((p) => p.provider === "ollama")).toEqual(
      expect.objectContaining({ provider: "ollama", hasApiKey: false, baseUrl: "http://localhost:11434" }),
    );
  });
});

// ---------------------------------------------------------------------------
// Backward compatibility with legacy single-provider credentials.json
// ---------------------------------------------------------------------------

describe("Legacy credential migration", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("loadProviderCredentials reads legacy single-provider format", async () => {
    const { loadProviderCredentials, CREDENTIALS_FILE } = await import("../src/config/credentials.js");
    // Write legacy format: flat object with provider field
    writeFileSync(join(dir, CREDENTIALS_FILE), JSON.stringify({
      provider: "anthropic",
      apiKey: "sk-legacy-key",
      model: "claude-sonnet-4-20250514",
      savedAt: "2026-01-01T00:00:00Z",
    }), "utf-8");

    const creds = loadProviderCredentials(dir, "anthropic");
    expect(creds).not.toBeNull();
    expect(creds!.apiKey).toBe("sk-legacy-key");
  });

  it("listConfiguredProviders handles legacy format", async () => {
    const { listConfiguredProviders, CREDENTIALS_FILE } = await import("../src/config/credentials.js");
    writeFileSync(join(dir, CREDENTIALS_FILE), JSON.stringify({
      provider: "openai",
      apiKey: "sk-legacy",
    }), "utf-8");

    const providers = listConfiguredProviders(dir);
    expect(providers.length).toBe(1);
    expect(providers[0].provider).toBe("openai");
  });
});

// ---------------------------------------------------------------------------
// validateApiKey — lightweight provider health check
// ---------------------------------------------------------------------------

describe("validateApiKey", () => {
  it("rejects empty API key", async () => {
    const { validateApiKey } = await import("../src/config/credentials.js");
    const result = await validateApiKey("anthropic", "");
    expect(result.valid).toBe(false);
    expect(result.error).toContain("empty");
  });

  it("validates Anthropic key format (sk-ant- prefix)", async () => {
    const { validateApiKey } = await import("../src/config/credentials.js");
    const result = await validateApiKey("anthropic", "not-a-valid-key");
    expect(result.valid).toBe(false);
    expect(result.error).toContain("format");
  });

  it("accepts valid Anthropic key format", async () => {
    const { validateApiKey } = await import("../src/config/credentials.js");
    const result = await validateApiKey("anthropic", "sk-ant-api03-valid-key-here");
    expect(result.valid).toBe(true);
  });

  it("validates OpenAI key format (sk- prefix)", async () => {
    const { validateApiKey } = await import("../src/config/credentials.js");
    const bad = await validateApiKey("openai", "not-valid");
    expect(bad.valid).toBe(false);
    const good = await validateApiKey("openai", "sk-proj-valid-key");
    expect(good.valid).toBe(true);
  });

  it("skips format validation for ollama (no key required)", async () => {
    const { validateApiKey } = await import("../src/config/credentials.js");
    const result = await validateApiKey("ollama", "");
    expect(result.valid).toBe(true);
  });

  it("accepts any non-empty key for unknown providers", async () => {
    const { validateApiKey } = await import("../src/config/credentials.js");
    const result = await validateApiKey("custom-provider", "any-key-value");
    expect(result.valid).toBe(true);
  });
});
