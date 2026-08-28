import { afterEach, describe, expect, it } from "vitest";
import {
  assertSecureServerBind,
  isExplicitlyAllowedServerOrigin,
  isLoopbackHost,
  isServerRequestAuthorized,
  resolveServerAllowedOrigins,
  resolveServerAuthToken,
  selectServerAuthSubprotocol,
  serverAuthSubprotocol,
  SERVER_ALLOWED_ORIGINS_ENV,
  SERVER_AUTH_TOKEN_ENV,
} from "../src/server/server-auth.js";

const TOKEN = "0123456789abcdef0123456789abcdef";

describe("control-plane server authentication", () => {
  afterEach(() => {
    delete process.env[SERVER_AUTH_TOKEN_ENV];
    delete process.env[SERVER_ALLOWED_ORIGINS_ENV];
  });

  it("refuses unauthenticated non-loopback binds", () => {
    expect(() => assertSecureServerBind("0.0.0.0", null)).toThrow(/Refusing to bind/);
    expect(() => assertSecureServerBind("example.test", null)).toThrow(/Refusing to bind/);
  });

  it("allows loopback binds without a token", () => {
    for (const host of ["localhost", "dev.localhost", "127.0.0.1", "127.20.30.40", "::1"]) {
      expect(isLoopbackHost(host)).toBe(true);
      expect(() => assertSecureServerBind(host, null)).not.toThrow();
    }
  });

  it("requires a strong configured token", () => {
    process.env[SERVER_AUTH_TOKEN_ENV] = "short";
    expect(() => resolveServerAuthToken()).toThrow(/at least 32 characters/);
    process.env[SERVER_AUTH_TOKEN_ENV] = TOKEN;
    expect(resolveServerAuthToken()).toBe(TOKEN);
  });

  it("authenticates HTTP with an exact bearer token", () => {
    expect(isServerRequestAuthorized({
      authToken: TOKEN,
      authorizationHeader: `Bearer ${TOKEN}`,
    })).toBe(true);
    expect(isServerRequestAuthorized({
      authToken: TOKEN,
      authorizationHeader: "Bearer wrong",
    })).toBe(false);
    expect(isServerRequestAuthorized({ authToken: TOKEN })).toBe(false);
  });

  it("uses an authenticated WebSocket subprotocol instead of URL credentials", () => {
    const protocol = serverAuthSubprotocol(TOKEN);
    expect(isServerRequestAuthorized({
      authToken: TOKEN,
      websocketProtocolHeader: protocol,
    })).toBe(true);
    expect(selectServerAuthSubprotocol(new Set(["unrelated", protocol]), TOKEN)).toBe(protocol);
    expect(isServerRequestAuthorized({
      authToken: TOKEN,
      websocketProtocolHeader: `${protocol}=`,
    })).toBe(false);
    expect(isServerRequestAuthorized({ authToken: TOKEN })).toBe(false);
  });

  it("parses an exact HTTPS browser-origin allowlist for reverse proxies", () => {
    process.env[SERVER_ALLOWED_ORIGINS_ENV] =
      "https://operator.example, https://operator.example:8443/";

    const allowed = resolveServerAllowedOrigins();

    expect([...allowed]).toEqual([
      "https://operator.example",
      "https://operator.example:8443",
    ]);
    expect(isExplicitlyAllowedServerOrigin("https://operator.example", allowed)).toBe(true);
    expect(isExplicitlyAllowedServerOrigin("http://operator.example", allowed)).toBe(false);
    expect(isExplicitlyAllowedServerOrigin("https://evil.example", allowed)).toBe(false);
  });

  it.each([
    "wss://operator.example",
    "https://*.operator.example",
    "https://user:secret@operator.example",
    "https://operator.example/control-plane",
    "https://operator.example?tenant=one",
    "https://operator.example/#fragment",
  ])("rejects non-origin server allowlist entry %s", (origin) => {
    expect(() => resolveServerAllowedOrigins([origin])).toThrow(
      SERVER_ALLOWED_ORIGINS_ENV,
    );
  });
});
