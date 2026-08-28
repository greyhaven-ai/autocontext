import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  statSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  CREDENTIALS_FILE,
  listConfiguredProviders,
  loadProviderCredentials,
  removeProviderCredentials,
  resolveApiKeyValue,
  saveProviderCredentials,
} from "../src/config/credential-store.js";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-credential-store-"));
}

describe("credential store workflow", () => {
  let dir: string;

  beforeEach(() => {
    dir = makeTempDir();
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("saves, loads, lists, and removes provider credentials with hardened file perms", () => {
    saveProviderCredentials(dir, "anthropic", { apiKey: "sk-ant-123", model: "claude" });
    saveProviderCredentials(dir, "openai", { apiKey: "sk-openai-456", baseUrl: "https://api.openai.com/v1" });

    expect(loadProviderCredentials(dir, "anthropic")).toMatchObject({
      apiKey: "sk-ant-123",
      model: "claude",
    });
    expect(listConfiguredProviders(dir)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ provider: "anthropic", hasApiKey: true }),
        expect.objectContaining({ provider: "openai", hasApiKey: true, baseUrl: "https://api.openai.com/v1" }),
      ]),
    );
    expect(removeProviderCredentials(dir, "anthropic")).toBe(true);
    expect(loadProviderCredentials(dir, "anthropic")).toBeNull();
    expect(statSync(join(dir, CREDENTIALS_FILE)).mode & 0o777).toBe(0o600);
  });

  it("reads legacy single-provider credential files", () => {
    writeFileSync(join(dir, CREDENTIALS_FILE), JSON.stringify({
      provider: "anthropic",
      apiKey: "sk-legacy-key",
      model: "claude-legacy",
    }), "utf-8");

    expect(loadProviderCredentials(dir, "anthropic")).toMatchObject({
      apiKey: "sk-legacy-key",
      model: "claude-legacy",
    });
  });

  it("returns literal API keys and rejects command-shaped values", () => {
    expect(resolveApiKeyValue("sk-ant-123")).toBe("sk-ant-123");
    expect(() => resolveApiKeyValue("!echo workflow-key")).toThrow(
      /command-based API key values are not supported/i,
    );
  });

  it("refuses invalid credentials before creating the store", () => {
    expect(() => saveProviderCredentials(dir, "anthropic", { apiKey: "bad-key" })).toThrow(
      /refusing to persist credentials.*invalid anthropic API key format/i,
    );
    expect(existsSync(join(dir, CREDENTIALS_FILE))).toBe(false);
  });

  it("refuses to follow a symbolic-link credential file", () => {
    if (process.platform === "win32") return;
    const outside = join(dir, "outside.json");
    writeFileSync(outside, "sentinel", "utf-8");
    symlinkSync(outside, join(dir, CREDENTIALS_FILE));

    expect(() => saveProviderCredentials(dir, "anthropic", {
      apiKey: "sk-ant-123",
    })).toThrow(/symbolic-link credential file/i);
    expect(readFileSync(outside, "utf-8")).toBe("sentinel");
  });

  it("fails closed when an existing store contains a command-shaped value", () => {
    writeFileSync(join(dir, CREDENTIALS_FILE), JSON.stringify({
      providers: {
        anthropic: { apiKey: "!echo workflow-key" },
      },
    }), "utf-8");

    expect(() => loadProviderCredentials(dir, "anthropic")).toThrow(
      /command-based API key values are not supported/i,
    );
  });
});
