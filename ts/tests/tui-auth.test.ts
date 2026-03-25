/**
 * Tests for AC-408: TUI /login, /logout, /provider, /whoami commands.
 *
 * Tests the protocol schemas and message handling, not the actual TUI rendering.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

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
    const status = handleTuiWhoami(dir);
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
