/**
 * Tests for AC-430 Phase 4: OAuth flows.
 *
 * - PKCE utilities (verifier, challenge, state)
 * - Local callback server
 * - Device code polling
 * - Token storage with expiry
 * - Token refresh logic
 * - OAuth provider configs
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createServer } from "node:http";

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-oauth-"));
}

// ---------------------------------------------------------------------------
// PKCE utilities
// ---------------------------------------------------------------------------

describe("PKCE utilities", () => {
  it("generatePKCE returns verifier and challenge", async () => {
    const { generatePKCE } = await import("../src/config/oauth.js");
    const pkce = generatePKCE();
    expect(pkce.verifier).toBeDefined();
    expect(pkce.challenge).toBeDefined();
    expect(pkce.verifier.length).toBeGreaterThanOrEqual(43);
    expect(pkce.challenge.length).toBeGreaterThan(0);
  });

  it("verifier and challenge are different", async () => {
    const { generatePKCE } = await import("../src/config/oauth.js");
    const pkce = generatePKCE();
    expect(pkce.verifier).not.toBe(pkce.challenge);
  });

  it("challenge is URL-safe base64", async () => {
    const { generatePKCE } = await import("../src/config/oauth.js");
    const pkce = generatePKCE();
    expect(pkce.challenge).toMatch(/^[A-Za-z0-9_-]+$/);
  });

  it("generateState returns a hex string", async () => {
    const { generateState } = await import("../src/config/oauth.js");
    const state = generateState();
    expect(state).toMatch(/^[a-f0-9]+$/);
    expect(state.length).toBe(32);
  });
});

// ---------------------------------------------------------------------------
// OAuth provider configs
// ---------------------------------------------------------------------------

describe("OAuth provider configs", () => {
  it("exports OAUTH_PROVIDERS with configs for known providers", async () => {
    const { OAUTH_PROVIDERS } = await import("../src/config/oauth.js");
    expect(OAUTH_PROVIDERS.anthropic).toBeDefined();
    expect(OAUTH_PROVIDERS.openai).toBeDefined();
    expect(OAUTH_PROVIDERS["github-copilot"]).toBeDefined();
    expect(OAUTH_PROVIDERS.gemini).toBeDefined();
  });

  it("anthropic config has correct endpoints and flow type", async () => {
    const { OAUTH_PROVIDERS } = await import("../src/config/oauth.js");
    const cfg = OAUTH_PROVIDERS.anthropic;
    expect(cfg.flow).toBe("authorization_code");
    expect(cfg.authorizationUrl).toContain("claude.ai");
    expect(cfg.tokenUrl).toContain("platform.claude.com");
    expect(cfg.clientId).toBeDefined();
    expect(cfg.scopes.length).toBeGreaterThan(0);
    expect(cfg.callbackPort).toBe(53692);
  });

  it("github-copilot config uses device_code flow", async () => {
    const { OAUTH_PROVIDERS } = await import("../src/config/oauth.js");
    const cfg = OAUTH_PROVIDERS["github-copilot"];
    expect(cfg.flow).toBe("device_code");
    expect(cfg.deviceCodeUrl).toBeDefined();
    expect(cfg.clientId).toBeDefined();
  });

  it("openai config has correct endpoints", async () => {
    const { OAUTH_PROVIDERS } = await import("../src/config/oauth.js");
    const cfg = OAUTH_PROVIDERS.openai;
    expect(cfg.flow).toBe("authorization_code");
    expect(cfg.authorizationUrl).toContain("auth.openai.com");
    expect(cfg.tokenUrl).toContain("auth.openai.com");
    expect(cfg.callbackPort).toBe(1455);
  });

  it("gemini config includes client secret", async () => {
    const { OAUTH_PROVIDERS } = await import("../src/config/oauth.js");
    const cfg = OAUTH_PROVIDERS.gemini;
    expect(cfg.flow).toBe("authorization_code");
    expect(cfg.clientSecret).toBeDefined();
    expect(cfg.callbackPort).toBe(8085);
  });

  it("isOAuthProvider returns true for OAuth-capable providers", async () => {
    const { isOAuthProvider } = await import("../src/config/oauth.js");
    expect(isOAuthProvider("anthropic")).toBe(true);
    expect(isOAuthProvider("openai")).toBe(true);
    expect(isOAuthProvider("github-copilot")).toBe(true);
    expect(isOAuthProvider("gemini")).toBe(true);
    expect(isOAuthProvider("ollama")).toBe(false);
    expect(isOAuthProvider("groq")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Local callback server
// ---------------------------------------------------------------------------

describe("Local callback server", () => {
  it("waitForCallback starts server and resolves with code from redirect", async () => {
    const { waitForCallback } = await import("../src/config/oauth.js");

    // Use a random high port to avoid conflicts
    const port = 49100 + Math.floor(Math.random() * 900);
    const callbackPath = "/callback";

    const promise = waitForCallback({ port, path: callbackPath, timeoutMs: 5000 });

    // Simulate browser redirect
    await new Promise<void>((resolve) => setTimeout(resolve, 100));
    const url = `http://127.0.0.1:${port}${callbackPath}?code=test-auth-code&state=test-state`;
    const res = await fetch(url);
    expect(res.ok).toBe(true);

    const result = await promise;
    expect(result.code).toBe("test-auth-code");
    expect(result.state).toBe("test-state");
  });

  it("waitForCallback times out if no redirect arrives", async () => {
    const { waitForCallback } = await import("../src/config/oauth.js");
    const port = 49200 + Math.floor(Math.random() * 900);

    await expect(
      waitForCallback({ port, path: "/callback", timeoutMs: 500 }),
    ).rejects.toThrow(/timeout/i);
  });
});

// ---------------------------------------------------------------------------
// OAuth token storage
// ---------------------------------------------------------------------------

describe("OAuth token storage", () => {
  let dir: string;
  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("saveOAuthTokens stores access/refresh/expiry", async () => {
    const { saveOAuthTokens, loadOAuthTokens } = await import("../src/config/oauth.js");
    saveOAuthTokens(dir, "anthropic", {
      accessToken: "access-123",
      refreshToken: "refresh-456",
      expiresAt: Date.now() + 3600_000,
    });

    const tokens = loadOAuthTokens(dir, "anthropic");
    expect(tokens).not.toBeNull();
    expect(tokens!.accessToken).toBe("access-123");
    expect(tokens!.refreshToken).toBe("refresh-456");
    expect(tokens!.expiresAt).toBeGreaterThan(Date.now());
  });

  it("isTokenExpired returns true when token is past expiry", async () => {
    const { isTokenExpired } = await import("../src/config/oauth.js");
    expect(isTokenExpired(Date.now() - 1000)).toBe(true);
  });

  it("isTokenExpired returns false when token is still valid", async () => {
    const { isTokenExpired } = await import("../src/config/oauth.js");
    // 30 minutes from now — well outside the 5-min buffer
    expect(isTokenExpired(Date.now() + 30 * 60_000)).toBe(false);
  });

  it("isTokenExpired accounts for buffer (expires within 5 min)", async () => {
    const { isTokenExpired } = await import("../src/config/oauth.js");
    // Token expires in 2 minutes — should be considered expired due to 5-min buffer
    expect(isTokenExpired(Date.now() + 2 * 60_000)).toBe(true);
  });

  it("loadOAuthTokens returns null when no tokens stored", async () => {
    const { loadOAuthTokens } = await import("../src/config/oauth.js");
    expect(loadOAuthTokens(dir, "anthropic")).toBeNull();
  });

  it("buildAuthorizationUrl constructs correct URL for Anthropic", async () => {
    const { buildAuthorizationUrl, OAUTH_PROVIDERS } = await import("../src/config/oauth.js");
    const cfg = OAUTH_PROVIDERS.anthropic;
    const url = buildAuthorizationUrl(cfg, {
      state: "test-state",
      codeChallenge: "test-challenge",
      redirectUri: "http://localhost:53692/callback",
    });
    expect(url).toContain("claude.ai/oauth/authorize");
    expect(url).toContain("client_id=" + cfg.clientId);
    expect(url).toContain("state=test-state");
    expect(url).toContain("code_challenge=test-challenge");
    expect(url).toContain("code_challenge_method=S256");
    expect(url).toContain("response_type=code");
  });
});
