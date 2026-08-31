import {
  chmodSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import {
  assertServerAllowedOriginsRequireAuthentication,
  assertSecureServerBind,
  isExplicitlyAllowedServerOrigin,
  isLoopbackHost,
  isServerRequestAuthorized,
  requiredInteractiveMessageCapabilities,
  requiredServerHttpCapabilities,
  resolveServerAllowedOrigins,
  resolveServerAuthToken,
  resolveServerCredentialsFile,
  selectServerAuthSubprotocol,
  serverAuthSubprotocol,
  serverAuthSubprotocolFromProof,
  serverAuthorizationHeader,
  ServerAuthenticator,
  ServerCredentialSigner,
  SERVER_ALLOW_TOKENLESS_LOOPBACK_ENV,
  SERVER_ALLOWED_ORIGINS_ENV,
  SERVER_AUTH_TOKEN_ENV,
  SERVER_CREDENTIALS_FILE_ENV,
} from "../src/server/server-auth.js";

const TOKEN = "0123456789abcdef0123456789abcdef";
const OTHER_TOKEN = "fedcba9876543210fedcba9876543210";
const NOW = 1_787_000_000;
const READ_REQUEST = {
  method: "GET",
  target: "/api/runs?limit=5&after=a%2Fb",
  origin: "",
  capabilities: ["control:read"] as const,
};
const temporaryDirectories: string[] = [];

function signer(
  secret = TOKEN,
  options: { keyId?: string; now?: number; jti?: string; ttlSeconds?: number } = {},
): ServerCredentialSigner {
  return new ServerCredentialSigner(secret, {
    keyId: options.keyId,
    nowSeconds: () => options.now ?? NOW,
    createJti: () => options.jti ?? "0123456789abcdef0123456789abcdef",
    ttlSeconds: options.ttlSeconds,
  });
}

function tempDirectory(): string {
  const directory = mkdtempSync(join(process.cwd(), ".autoctx-auth-"));
  temporaryDirectories.push(directory);
  return directory;
}

describe("control-plane server authentication", () => {
  afterEach(() => {
    delete process.env[SERVER_AUTH_TOKEN_ENV];
    delete process.env[SERVER_ALLOW_TOKENLESS_LOOPBACK_ENV];
    delete process.env[SERVER_ALLOWED_ORIGINS_ENV];
    delete process.env[SERVER_CREDENTIALS_FILE_ENV];
    for (const directory of temporaryDirectories.splice(0)) {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("refuses unauthenticated non-loopback binds", () => {
    expect(() => assertSecureServerBind("0.0.0.0", null)).toThrow(/Refusing to bind/);
    expect(() => assertSecureServerBind("example.test", null)).toThrow(/Refusing to bind/);
  });

  it("requires explicit tokenless-loopback opt-in while recognizing every loopback spelling", () => {
    for (const host of ["localhost", "dev.localhost", "127.0.0.1", "127.20.30.40", "::1"]) {
      expect(isLoopbackHost(host)).toBe(true);
      expect(() => assertSecureServerBind(host, null, false)).toThrow(/Refusing to bind/);
      expect(() => assertSecureServerBind(host, null, true)).not.toThrow();
    }
    process.env[SERVER_ALLOW_TOKENLESS_LOOPBACK_ENV] = "1";
    expect(() => assertSecureServerBind("127.0.0.1", null)).not.toThrow();
  });

  it("requires a bounded strong configured HMAC secret", () => {
    process.env[SERVER_AUTH_TOKEN_ENV] = "short";
    expect(() => resolveServerAuthToken()).toThrow(/at least 32 bytes/);
    process.env[SERVER_AUTH_TOKEN_ENV] = TOKEN;
    expect(resolveServerAuthToken()).toBe(TOKEN);
    process.env[SERVER_AUTH_TOKEN_ENV] = "x".repeat(4_097);
    expect(() => resolveServerAuthToken()).toThrow(/too large/);
  });

  it("emits the canonical actx1 contract and authenticates a server-owned principal", () => {
    const proof = signer().signRequest(READ_REQUEST);
    const [, encodedClaims] = proof.split(".");
    expect(JSON.parse(Buffer.from(encodedClaims!, "base64url").toString("utf8"))).toEqual({
      aud: "autocontext-control-plane",
      caps: ["control:read"],
      exp: NOW + 60,
      iat: NOW,
      jti: "0123456789abcdef0123456789abcdef",
      kid: "env",
      method: "GET",
      origin: "",
      target: "/api/runs?limit=5&after=a%2Fb",
      v: 1,
    });
    expect(proof).toMatch(/^actx1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/);

    const authenticator = new ServerAuthenticator({
      authToken: TOKEN,
      nowSeconds: () => NOW,
    });
    const result = authenticator.authenticateRequest({
      ...READ_REQUEST,
      authorizationHeader: `Bearer ${proof}`,
    });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.principal).toMatchObject({
        id: "host-operator",
        keyId: "env",
        expiresAt: NOW + 60,
        insecure: false,
      });
      expect([...result.principal.capabilities]).toEqual(["control:read"]);
      expect(authenticator.principalHasCapabilities(result.principal, ["control:read"])).toBe(true);
    }
  });

  it("matches the fixed Python actx1 proof vector", () => {
    const proof = new ServerCredentialSigner(TOKEN, {
      keyId: "env",
      ttlSeconds: 60,
      nowSeconds: () => 1_700_000_000,
      createJti: () => "00112233445566778899aabbccddeeff",
    }).signRequest({
      method: "POST",
      target: "/api/runs?limit=5",
      capabilities: ["host:execute", "control:operate"],
      origin: "http://localhost:1420",
    });
    expect(proof).toBe(
      "actx1.eyJhdWQiOiJhdXRvY29udGV4dC1jb250cm9sLXBsYW5lIiwiY2FwcyI6WyJjb250cm9sOm9wZXJhdGUi"
      + "LCJob3N0OmV4ZWN1dGUiXSwiZXhwIjoxNzAwMDAwMDYwLCJpYXQiOjE3MDAwMDAwMDAsImp0aSI6IjAwMTEyMjMz"
      + "NDQ1NTY2Nzc4ODk5YWFiYmNjZGRlZWZmIiwia2lkIjoiZW52IiwibWV0aG9kIjoiUE9TVCIsIm9yaWdpbiI6Imh0"
      + "dHA6Ly9sb2NhbGhvc3Q6MTQyMCIsInRhcmdldCI6Ii9hcGkvcnVucz9saW1pdD01IiwidiI6MX0.IMuu4_lqAKVm"
      + "JeV-sV6YlBeFwGW4j-c7Wl71kMESD8c",
    );
  });

  it("expires proof-bound principals after the handshake", () => {
    let now = NOW;
    const authenticator = new ServerAuthenticator({
      authToken: TOKEN,
      nowSeconds: () => now,
    });
    const proof = signer(TOKEN, { ttlSeconds: 1 }).signRequest(READ_REQUEST);
    const result = authenticator.authenticateRequest({
      ...READ_REQUEST,
      authorizationHeader: `Bearer ${proof}`,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    now += 1;
    expect(authenticator.principalHasCapabilities(result.principal, ["control:read"])).toBe(false);
  });

  it("treats a credential notAfter second as inclusive", () => {
    let now = 1_030;
    const request = {
      method: "GET",
      target: "/api/runs",
      capabilities: ["control:read"] as const,
    };
    const authenticator = new ServerAuthenticator({
      credentials: [{
        keyId: "operator-1",
        principalId: "test-operator",
        secret: TOKEN,
        capabilities: ["control:read"],
        notAfter: 1_030,
      }],
      nowSeconds: () => now,
    });
    const proof = new ServerCredentialSigner(TOKEN, {
      keyId: "operator-1",
      ttlSeconds: 60,
      nowSeconds: () => 1_000,
      createJti: () => "00000000000000000000000000000008",
    }).signRequest(request);
    const result = authenticator.authenticateRequest({
      ...request,
      authorizationHeader: `Bearer ${proof}`,
    });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.principal.expiresAt).toBe(1_031);
    expect(authenticator.principalHasCapabilities(result.principal, ["control:read"])).toBe(true);
    now = 1_031;
    expect(authenticator.principalHasCapabilities(result.principal, ["control:read"])).toBe(false);
  });

  it("binds proofs to method, raw target, origin, audience, time, and signature", () => {
    const proof = signer().signRequest(READ_REQUEST);
    const cases = [
      { ...READ_REQUEST, method: "POST" },
      { ...READ_REQUEST, target: "/api/runs?after=a%2Fb&limit=5" },
      { ...READ_REQUEST, target: "/api/runs?limit=5&after=a/b" },
      { ...READ_REQUEST, origin: "https://operator.example" },
      { ...READ_REQUEST, audience: "other-service" },
    ];
    for (const request of cases) {
      const authenticator = new ServerAuthenticator({
        authToken: TOKEN,
        nowSeconds: () => NOW,
      });
      expect(authenticator.authenticateRequest({
        ...request,
        authorizationHeader: `Bearer ${proof}`,
      })).toEqual({ ok: false, status: 401 });
    }
    const wrongKey = new ServerAuthenticator({
      authToken: OTHER_TOKEN,
      nowSeconds: () => NOW,
    });
    expect(wrongKey.authenticateRequest({
      ...READ_REQUEST,
      authorizationHeader: `Bearer ${proof}`,
    })).toEqual({ ok: false, status: 401 });

    for (const proofTime of [NOW - 66, NOW + 6]) {
      const timedProof = signer(TOKEN, { now: proofTime }).signRequest(READ_REQUEST);
      const authenticator = new ServerAuthenticator({
        authToken: TOKEN,
        nowSeconds: () => NOW,
      });
      expect(authenticator.authenticateRequest({
        ...READ_REQUEST,
        authorizationHeader: `Bearer ${timedProof}`,
      })).toEqual({ ok: false, status: 401 });
    }
  });

  it("atomically consumes each key/JTI pair and fails closed when replay storage is full", () => {
    let now = NOW;
    const authenticator = new ServerAuthenticator({
      authToken: TOKEN,
      nowSeconds: () => now,
      replayCapacity: 1,
    });
    const first = signer(TOKEN, { ttlSeconds: 1 }).signRequest(READ_REQUEST);
    expect(authenticator.authenticateRequest({
      ...READ_REQUEST,
      authorizationHeader: `Bearer ${first}`,
    }).ok).toBe(true);
    expect(authenticator.authenticateRequest({
      ...READ_REQUEST,
      authorizationHeader: `Bearer ${first}`,
    })).toEqual({ ok: false, status: 401 });

    const second = signer(TOKEN, {
      jti: "11111111111111111111111111111111",
      ttlSeconds: 1,
    }).signRequest(READ_REQUEST);
    expect(authenticator.authenticateRequest({
      ...READ_REQUEST,
      authorizationHeader: `Bearer ${second}`,
    })).toEqual({ ok: false, status: 401 });

    now += 7;
    const third = signer(TOKEN, {
      now,
      jti: "22222222222222222222222222222222",
      ttlSeconds: 1,
    }).signRequest(READ_REQUEST);
    expect(authenticator.authenticateRequest({
      ...READ_REQUEST,
      authorizationHeader: `Bearer ${third}`,
    }).ok).toBe(true);
  });

  it("enforces route capabilities against the proof and the configured key ceiling", () => {
    const authenticator = new ServerAuthenticator({
      credentials: [{
        keyId: "viewer",
        principalId: "dashboard-viewer",
        secret: TOKEN,
        capabilities: ["control:read"],
      }],
      nowSeconds: () => NOW,
    });
    const readAndOperate = signer(TOKEN, { keyId: "viewer" }).signRequest({
      ...READ_REQUEST,
      capabilities: ["control:operate", "control:read"],
    });
    expect(authenticator.authenticateRequest({
      ...READ_REQUEST,
      authorizationHeader: `Bearer ${readAndOperate}`,
    })).toEqual({ ok: false, status: 403 });

    const read = signer(TOKEN, {
      keyId: "viewer",
      jti: "33333333333333333333333333333333",
    }).signRequest(READ_REQUEST);
    expect(authenticator.authenticateRequest({
      ...READ_REQUEST,
      capabilities: ["control:operate"],
      authorizationHeader: `Bearer ${read}`,
    })).toEqual({ ok: false, status: 403 });
  });

  it("separates content reads, host execution, and administrative commands", () => {
    expect(requiredServerHttpCapabilities("GET", "/")).toEqual(["control:read"]);
    expect(requiredServerHttpCapabilities("GET", "/api/runs?limit=5")).toEqual([
      "control:read",
      "content:read",
    ]);
    expect(requiredServerHttpCapabilities("POST", "/api/knowledge/solve")).toEqual([
      "control:operate",
      "content:read",
      "host:execute",
    ]);
    expect(requiredServerHttpCapabilities("GET", "/api/knowledge/solve/job-1")).toEqual([
      "control:read",
      "content:read",
      "host:execute",
    ]);
    expect(requiredServerHttpCapabilities("POST", "/api/knowledge/import")).toEqual([
      "control:operate",
      "content:read",
      "host:execute",
    ]);
    for (const target of [
      "/api/hub/packages/from-run/run-1",
      "/api/hub/results/from-run/run-1",
      "/api/hub/packages/pkg-1/adopt",
    ]) {
      expect(requiredServerHttpCapabilities("POST", target)).toEqual([
        "control:operate",
        "content:read",
        "host:execute",
      ]);
    }
    for (const target of [
      "/api/openclaw/discovery/capabilities",
      "/api/openclaw/discovery/scenario/grid_ctf",
      "/api/openclaw/skill/manifest",
    ]) {
      expect(requiredServerHttpCapabilities("GET", target)).toEqual([
        "control:read",
        "content:read",
        "host:execute",
      ]);
    }
    for (const target of ["/api/openclaw/artifacts", "/api/openclaw/artifacts/"]) {
      expect(requiredServerHttpCapabilities("POST", target)).toEqual([
        "control:operate",
        "content:read",
        "host:execute",
      ]);
    }
    expect(requiredServerHttpCapabilities("PUT", "/api/knowledge/grid_ctf")).toEqual([
      "control:operate",
      "content:read",
    ]);
    expect(requiredInteractiveMessageCapabilities("start_run")).toEqual([
      "control:operate",
      "host:execute",
    ]);
    expect(requiredInteractiveMessageCapabilities("resume")).toEqual([
      "control:operate",
      "host:execute",
    ]);
    expect(requiredInteractiveMessageCapabilities("override_gate")).toEqual([
      "control:operate",
      "host:execute",
    ]);
    expect(requiredInteractiveMessageCapabilities("stop")).toEqual(["control:operate"]);
    expect(requiredInteractiveMessageCapabilities("switch_provider")).toEqual(["control:admin"]);
  });

  it.each([
    "/x/../api/runs",
    "/api/x/../knowledge/solve",
    "/api/%2e%2e/knowledge/solve",
    "//attacker.example/api/runs",
    "/api\\runs",
  ])("rejects request targets whose path would be normalized: %s", (target) => {
    expect(() => requiredServerHttpCapabilities("GET", target)).toThrow(
      /normalized path segments/,
    );
    expect(new ServerAuthenticator({ authToken: TOKEN }).authenticateRequest({
      method: "GET",
      target,
      capabilities: ["control:read"],
    })).toEqual({ ok: false, status: 401 });
  });

  it("admin implies control operations but not content access or host execution", () => {
    const authenticator = new ServerAuthenticator({
      credentials: [{
        keyId: "operator",
        principalId: "alice",
        secret: TOKEN,
        capabilities: ["control:admin"],
      }],
      nowSeconds: () => NOW,
    });
    const proof = signer(TOKEN, { keyId: "operator" }).signRequest({
      method: "GET",
      target: "/ws/interactive",
      capabilities: ["control:admin"],
    });
    const result = authenticator.authenticateRequest({
      method: "GET",
      target: "/ws/interactive",
      capabilities: ["control:operate"],
      authorizationHeader: `Bearer ${proof}`,
    });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect([...result.principal.capabilities]).toEqual([
        "control:admin",
        "control:operate",
        "control:read",
      ]);
      expect(authenticator.principalHasCapabilities(result.principal, ["content:read"])).toBe(false);
      expect(authenticator.principalHasCapabilities(result.principal, ["host:execute"])).toBe(false);
    }
  });

  it("uses the exact one-use proof as the WebSocket subprotocol", () => {
    const proof = signer().signRequest({
      method: "GET",
      target: "/ws/interactive",
      capabilities: ["control:operate"],
    });
    expect(serverAuthSubprotocolFromProof(proof)).toBe(proof);
    expect(selectServerAuthSubprotocol(new Set(["unrelated", proof]))).toBe(proof);
    const defaultProof = serverAuthSubprotocol(TOKEN);
    expect(defaultProof).toMatch(/^actx1\./);
    const defaultClaims = JSON.parse(Buffer.from(defaultProof.split(".")[1]!, "base64url").toString());
    expect(defaultClaims.caps).toEqual(["content:read", "control:operate", "host:execute"]);
    expect(selectServerAuthSubprotocol(new Set([`${proof}=`]))).toBeNull();
    const liveProof = serverAuthSubprotocol(TOKEN);
    expect(isServerRequestAuthorized({
      authToken: TOKEN,
      websocketProtocolHeader: liveProof,
    })).toBe(false);
    expect(isServerRequestAuthorized({
      authToken: TOKEN,
      authorizationHeader: `Bearer ${TOKEN}`,
    })).toBe(false);
    expect(isServerRequestAuthorized({ authToken: null })).toBe(false);
  });

  it("loads a secure capability-scoped credential registry", () => {
    const directory = tempDirectory();
    const path = join(directory, "credentials.json");
    writeFileSync(path, JSON.stringify({
      version: 1,
      credentials: [{
        kid: "viewer",
        principal: "dashboard-viewer",
        secret: TOKEN,
        capabilities: ["control:read"],
        not_before: NOW - 10,
        not_after: NOW + 10,
        disabled: false,
      }],
    }));
    chmodSync(path, 0o600);
    process.env[SERVER_CREDENTIALS_FILE_ENV] = path;

    expect(resolveServerCredentialsFile()).toEqual([{
      keyId: "viewer",
      principalId: "dashboard-viewer",
      secret: TOKEN,
      capabilities: ["control:read"],
      notBefore: NOW - 10,
      notAfter: NOW + 10,
      disabled: false,
    }]);
  });

  it("fails closed on Windows until credential-registry DACLs can be validated", () => {
    const descriptor = Object.getOwnPropertyDescriptor(process, "platform");
    Object.defineProperty(process, "platform", { configurable: true, value: "win32" });
    try {
      expect(() => resolveServerCredentialsFile("C:\\secure\\credentials.json")).toThrow(
        /unsupported on Windows.*DACL.*AUTOCONTEXT_SERVER_TOKEN/,
      );
    } finally {
      if (descriptor !== undefined) Object.defineProperty(process, "platform", descriptor);
    }
  });

  it("rejects permissive, linked, and malformed credential registries", () => {
    const directory = tempDirectory();
    const path = join(directory, "credentials.json");
    writeFileSync(path, JSON.stringify({ version: 1, credentials: [] }));
    chmodSync(path, 0o644);
    expect(() => resolveServerCredentialsFile(path)).toThrow(/permissions/);

    chmodSync(path, 0o600);
    const linked = join(directory, "linked.json");
    symlinkSync(path, linked);
    expect(() => resolveServerCredentialsFile(linked)).toThrow(/symlink/);

    writeFileSync(path, JSON.stringify({ version: 1, credentials: [], extra: true }));
    expect(() => resolveServerCredentialsFile(path)).toThrow(/only version and credentials/);

    const writableParent = join(directory, "writable");
    mkdirSync(writableParent, { mode: 0o700 });
    const exposed = join(writableParent, "credentials.json");
    writeFileSync(exposed, JSON.stringify({ version: 1, credentials: [] }));
    chmodSync(exposed, 0o600);
    chmodSync(writableParent, 0o777);
    expect(() => resolveServerCredentialsFile(exposed)).toThrow(/parent is group\/world writable/);
  });

  it("validates a POSIX parent whose literal name contains a backslash", () => {
    if (process.platform === "win32") return;
    const directory = tempDirectory();
    const exposedParent = join(directory, "literal\\backslash");
    mkdirSync(exposedParent, { mode: 0o700 });
    chmodSync(exposedParent, 0o777);
    const path = join(exposedParent, "credentials.json");
    writeFileSync(path, JSON.stringify({ version: 1, credentials: [] }));
    chmodSync(path, 0o600);

    expect(() => resolveServerCredentialsFile(path)).toThrow(/parent is group\/world writable/);
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
    expect(() => assertServerAllowedOriginsRequireAuthentication(false, allowed)).toThrow(
      /require control-plane credentials/,
    );
    expect(() => assertServerAllowedOriginsRequireAuthentication(true, allowed)).not.toThrow();
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

  it("creates a fresh signed Authorization proof helper", () => {
    const header = serverAuthorizationHeader(TOKEN, READ_REQUEST, {
      nowSeconds: () => NOW,
      createJti: () => "44444444444444444444444444444444",
    });
    expect(header).toMatch(/^Bearer actx1\./);
    expect(header).not.toContain(TOKEN);
  });
});
