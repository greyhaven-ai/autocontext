import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

import {
  loadPersistedCredentials,
  readStoredCredentialEntry,
  resolveConfigDir,
} from "../src/config/persisted-credentials.js";
import { CREDENTIALS_FILE } from "../src/config/credentials.js";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-persisted-creds-"));
}

describe("persisted credentials workflow", () => {
  const savedConfigDir = process.env.AUTOCONTEXT_CONFIG_DIR;
  const savedHome = process.env.HOME;
  let dir: string;

  beforeEach(() => {
    dir = makeTempDir();
    delete process.env.AUTOCONTEXT_CONFIG_DIR;
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
    if (savedConfigDir === undefined) delete process.env.AUTOCONTEXT_CONFIG_DIR;
    else process.env.AUTOCONTEXT_CONFIG_DIR = savedConfigDir;
    if (savedHome === undefined) delete process.env.HOME;
    else process.env.HOME = savedHome;
  });

  it("resolves config directories from explicit, env, and HOME defaults", () => {
    process.env.AUTOCONTEXT_CONFIG_DIR = "/tmp/from-env";
    process.env.HOME = "/tmp/home";

    expect(resolveConfigDir("/tmp/explicit")).toBe("/tmp/explicit");
    expect(resolveConfigDir()).toBe("/tmp/from-env");

    delete process.env.AUTOCONTEXT_CONFIG_DIR;
    expect(resolveConfigDir()).toBe("/tmp/home/.config/autoctx");
  });

  it("loads requested providers from multi-provider credentials.json", () => {
    writeFileSync(join(dir, CREDENTIALS_FILE), JSON.stringify({
      providers: {
        anthropic: { apiKey: "sk-ant-stored", model: "claude" },
        openai: { apiKey: "sk-openai-stored", baseUrl: "https://api.openai.com/v1" },
      },
    }));

    expect(loadPersistedCredentials(dir)).toEqual({
      provider: "anthropic",
      apiKey: "sk-ant-stored",
      model: "claude",
    });
    expect(loadPersistedCredentials(dir, "openai")).toEqual({
      provider: "openai",
      apiKey: "sk-openai-stored",
      baseUrl: "https://api.openai.com/v1",
    });
    expect(loadPersistedCredentials(dir, "missing")).toBeNull();
  });

  it("fails closed on command-shaped API keys in legacy credential files", () => {
    writeFileSync(join(dir, CREDENTIALS_FILE), JSON.stringify({
      provider: "anthropic",
      apiKey: "!printf 'sk-shell'",
      model: "claude-opus",
      savedAt: "2026-04-10T00:00:00Z",
    }));

    expect(() => loadPersistedCredentials(dir, "anthropic")).toThrow(
      /command-based API key values are not supported/i,
    );
  });

  it("normalizes trimmed stored credential entries", () => {
    expect(readStoredCredentialEntry("anthropic", {
      apiKey: "  sk-ant-trim  ",
      model: " claude ",
      baseUrl: " https://api.example.com ",
      savedAt: " 2026-04-10T00:00:00Z ",
    })).toEqual({
      provider: "anthropic",
      apiKey: "sk-ant-trim",
      model: "claude",
      baseUrl: "https://api.example.com",
      savedAt: "2026-04-10T00:00:00Z",
    });
  });
});
