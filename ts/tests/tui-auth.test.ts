/**
 * Tests for AC-408: TUI /login, /logout, /provider, /whoami commands.
 *
 * Covers protocol schemas, shared auth helpers, and the slash-command handler
 * used by the interactive TUI submit path.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import type { RunManager } from "../src/server/run-manager.js";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-tui-auth-"));
}

// ---------------------------------------------------------------------------
// Protocol schemas: new auth command types
// ---------------------------------------------------------------------------

describe("Auth protocol schemas", () => {
  it("LoginCmdSchema parses login command with provider", async () => {
    const { LoginCmdSchema } = await import("../src/server/protocol.js");
    const msg = LoginCmdSchema.parse({ type: "login", provider: "anthropic", apiKey: "sk-ant-123" });
    expect(msg.type).toBe("login");
    expect(msg.provider).toBe("anthropic");
    expect(msg.apiKey).toBe("sk-ant-123");
  });

  it("LoginCmdSchema allows login without apiKey (for ollama)", async () => {
    const { LoginCmdSchema } = await import("../src/server/protocol.js");
    const msg = LoginCmdSchema.parse({ type: "login", provider: "ollama" });
    expect(msg.provider).toBe("ollama");
    expect(msg.apiKey).toBeUndefined();
  });

  it("LogoutCmdSchema parses logout command", async () => {
    const { LogoutCmdSchema } = await import("../src/server/protocol.js");
    const msg = LogoutCmdSchema.parse({ type: "logout" });
    expect(msg.type).toBe("logout");
  });

  it("LogoutCmdSchema accepts optional provider for selective logout", async () => {
    const { LogoutCmdSchema } = await import("../src/server/protocol.js");
    const msg = LogoutCmdSchema.parse({ type: "logout", provider: "anthropic" });
    expect(msg.provider).toBe("anthropic");
  });

  it("SwitchProviderCmdSchema parses provider switch", async () => {
    const { SwitchProviderCmdSchema } = await import("../src/server/protocol.js");
    const msg = SwitchProviderCmdSchema.parse({ type: "switch_provider", provider: "openai" });
    expect(msg.provider).toBe("openai");
  });

  it("WhoamiCmdSchema parses whoami request", async () => {
    const { WhoamiCmdSchema } = await import("../src/server/protocol.js");
    const msg = WhoamiCmdSchema.parse({ type: "whoami" });
    expect(msg.type).toBe("whoami");
  });

  it("AuthStatusMsgSchema parses server auth status response", async () => {
    const { AuthStatusMsgSchema } = await import("../src/server/protocol.js");
    const msg = AuthStatusMsgSchema.parse({
      type: "auth_status",
      provider: "anthropic",
      authenticated: true,
      model: "claude-sonnet-4-20250514",
    });
    expect(msg.type).toBe("auth_status");
    expect(msg.provider).toBe("anthropic");
    expect(msg.authenticated).toBe(true);
  });

  it("new auth commands are included in ClientMessageSchema", async () => {
    const { parseClientMessage } = await import("../src/server/protocol.js");
    expect(() => parseClientMessage({ type: "login", provider: "anthropic" })).not.toThrow();
    expect(() => parseClientMessage({ type: "logout" })).not.toThrow();
    expect(() => parseClientMessage({ type: "switch_provider", provider: "openai" })).not.toThrow();
    expect(() => parseClientMessage({ type: "whoami" })).not.toThrow();
  });

  it("AuthStatusMsgSchema is included in ServerMessageSchema", async () => {
    const { parseServerMessage } = await import("../src/server/protocol.js");
    expect(() => parseServerMessage({
      type: "auth_status",
      provider: "anthropic",
      authenticated: true,
    })).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// Credential operations from TUI context
// ---------------------------------------------------------------------------

describe("TUI auth credential operations", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("handleTuiLogin saves credentials to shared store", async () => {
    const { handleTuiLogin, handleTuiWhoami } = await import("../src/server/tui-auth.js");
    await handleTuiLogin(dir, "anthropic", "sk-ant-test-123");
    const status = handleTuiWhoami(dir);
    expect(status.provider).toBe("anthropic");
    expect(status.authenticated).toBe(true);
  });

  it("handleTuiLogout removes credentials", async () => {
    const { handleTuiLogin, handleTuiLogout, handleTuiWhoami } = await import("../src/server/tui-auth.js");
    await handleTuiLogin(dir, "anthropic", "sk-ant-test-123");
    handleTuiLogout(dir, "anthropic");
    const status = handleTuiWhoami(dir);
    expect(status.authenticated).toBe(false);
  });

  it("handleTuiLogout without provider clears all credentials", async () => {
    const { handleTuiLogin, handleTuiLogout, handleTuiWhoami } = await import("../src/server/tui-auth.js");
    await handleTuiLogin(dir, "anthropic", "sk-ant-test");
    await handleTuiLogin(dir, "openai", "sk-test");
    handleTuiLogout(dir);
    const status = handleTuiWhoami(dir);
    expect(status.authenticated).toBe(false);
  });

  it("handleTuiWhoami returns provider and model info", async () => {
    const { handleTuiLogin, handleTuiWhoami } = await import("../src/server/tui-auth.js");
    await handleTuiLogin(dir, "anthropic", "sk-ant-test", "claude-sonnet-4-20250514");
    const status = handleTuiWhoami(dir);
    expect(status.provider).toBe("anthropic");
    expect(status.model).toBe("claude-sonnet-4-20250514");
    expect(status.authenticated).toBe(true);
  });

  it("handleTuiSwitchProvider changes the active provider", async () => {
    const { handleTuiLogin, handleTuiSwitchProvider, handleTuiWhoami } = await import("../src/server/tui-auth.js");
    await handleTuiLogin(dir, "anthropic", "sk-ant-test");
    await handleTuiLogin(dir, "openai", "sk-openai-test");
    handleTuiSwitchProvider(dir, "openai");
    const status = handleTuiWhoami(dir, "openai");
    expect(status.provider).toBe("openai");
    expect(status.authenticated).toBe(true);
  });

  it("handleTuiLogin validates key format and returns result", async () => {
    const { handleTuiLogin } = await import("../src/server/tui-auth.js");
    const result = await handleTuiLogin(dir, "anthropic", "bad-key");
    expect(result.saved).toBe(true);
    expect(result.validationWarning).toBeDefined();
  });
});

function makeMockManager() {
  let activeProvider: string | null = null;
  return {
    pause() {},
    resume() {},
    listScenarios() {
      return ["grid_ctf"];
    },
    async startRun() {
      return "run_123";
    },
    injectHint() {},
    overrideGate() {},
    async chatAgent() {
      return "## Findings\nMock response";
    },
    setActiveProvider(config: { providerType: string }) {
      activeProvider = config.providerType;
    },
    clearActiveProvider() {
      activeProvider = null;
    },
    getActiveProviderType() {
      return activeProvider;
    },
  } satisfies Partial<RunManager>;
}

describe("Interactive TUI command handler", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("routes /login and /provider through the live slash-command handler", async () => {
    const { handleInteractiveTuiCommand } = await import("../src/tui/commands.js");
    // Deliberately partial test double. `satisfies Partial<RunManager>` in
    // makeMockManager still type-checks every method it DOES implement
    // against the real class (that is the drift protection AC-909 wants);
    // widening through unknown here only covers the members this code path
    // never touches. Replacing the pre-existing `as RunManager`, which only
    // compiled because tests were previously unchecked.
    const manager = makeMockManager() as unknown as RunManager;

    let result = await handleInteractiveTuiCommand({
      manager,
      configDir: dir,
      raw: "/login anthropic",
      pendingLogin: null,
    });
    expect(result.logLines[0]).toContain("enter API key for anthropic");
    expect(result.pendingLogin?.provider).toBe("anthropic");

    result = await handleInteractiveTuiCommand({
      manager,
      configDir: dir,
      raw: "sk-ant-test-123",
      pendingLogin: result.pendingLogin,
    });
    expect(result.logLines[0]).toBe("logged in to anthropic");
    expect(manager.getActiveProviderType()).toBe("anthropic");
    expect(result.pendingLogin).toBeNull();

    result = await handleInteractiveTuiCommand({
      manager,
      configDir: dir,
      raw: "/provider deterministic",
      pendingLogin: null,
    });
    expect(result.logLines[0]).toBe("active provider: deterministic");
    expect(manager.getActiveProviderType()).toBe("deterministic");

    result = await handleInteractiveTuiCommand({
      manager,
      configDir: dir,
      raw: "/whoami",
      pendingLogin: null,
    });
    expect(result.logLines).toContain("provider: deterministic");
    expect(result.logLines).toContain("authenticated: yes");
  });
});
