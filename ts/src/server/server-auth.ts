import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import {
  closeSync,
  constants as fsConstants,
  fstatSync,
  lstatSync,
  openSync,
  readSync,
  type Stats,
} from "node:fs";
import { isAbsolute, join, parse, relative, sep } from "node:path";

export const SERVER_AUTH_TOKEN_ENV = "AUTOCONTEXT_SERVER_TOKEN";
export const SERVER_CREDENTIALS_FILE_ENV = "AUTOCONTEXT_SERVER_CREDENTIALS_FILE";
export const SERVER_ALLOW_TOKENLESS_LOOPBACK_ENV = "AUTOCONTEXT_ALLOW_TOKENLESS_LOOPBACK";
export const SERVER_ALLOWED_ORIGINS_ENV = "AUTOCONTEXT_SERVER_ALLOWED_ORIGINS";
export const SERVER_AUTH_SUBPROTOCOL_PREFIX = "actx1.";
export const SERVER_AUTH_AUDIENCE = "autocontext-control-plane";
export const SERVER_AUTH_PROOF_VERSION = 1;
export const SERVER_AUTH_MAX_TTL_SECONDS = 60;
export const SERVER_AUTH_CLOCK_SKEW_SECONDS = 5;
export const SERVER_AUTH_DEFAULT_TTL_SECONDS = 60;
export const SERVER_AUTH_DEFAULT_REPLAY_CAPACITY = 8_192;
export const SERVER_AUTH_IMPLICIT_KEY_ID = "env";

export const SERVER_CAPABILITIES = [
  "content:read",
  "control:admin",
  "control:operate",
  "control:read",
  "host:execute",
] as const;

export type ServerCapability = (typeof SERVER_CAPABILITIES)[number];

const SERVER_CAPABILITY_SET = new Set<string>(SERVER_CAPABILITIES);
const MIN_SERVER_AUTH_TOKEN_LENGTH = 32;
const MAX_SERVER_AUTH_SECRET_BYTES = 4_096;
const MAX_SERVER_CREDENTIALS_FILE_BYTES = 64 * 1_024;
const MAX_SERVER_CREDENTIALS = 256;
const AUTH_PROOF_PREFIX = "actx1";
const AUTH_PROOF_CLAIMS = [
  "aud",
  "caps",
  "exp",
  "iat",
  "jti",
  "kid",
  "method",
  "origin",
  "target",
  "v",
] as const;
const DUMMY_AUTH_SECRET = Buffer.alloc(32, 0xa5);

export interface ServerAuthClaims {
  readonly v: 1;
  readonly kid: string;
  readonly iat: number;
  readonly exp: number;
  readonly jti: string;
  readonly caps: readonly ServerCapability[];
  readonly method: string;
  readonly target: string;
  readonly origin: string;
  readonly aud: string;
}

export interface ServerCredentialConfig {
  readonly keyId: string;
  readonly secret: string;
  readonly principalId: string;
  readonly capabilities: readonly ServerCapability[];
  /** Inclusive Unix epoch second at which this key becomes usable. */
  readonly notBefore?: number;
  /** Inclusive Unix epoch second through which this key remains usable. */
  readonly notAfter?: number;
  readonly disabled?: boolean;
}

export interface ServerPrincipal {
  readonly id: string;
  readonly keyId: string;
  readonly capabilities: ReadonlySet<ServerCapability>;
  readonly authenticatedAt: number;
  /** Unix epoch second when this proof-bound principal ceases to be valid. */
  readonly expiresAt: number | null;
  readonly insecure: boolean;
}

export interface ServerProofRequest {
  readonly method: string;
  /** The raw request target as transmitted, including its query string. */
  readonly target: string;
  readonly capabilities: readonly ServerCapability[];
  readonly origin?: string;
  readonly audience?: string;
}

export interface ServerRequestSigner {
  signRequest(request: ServerProofRequest): string;
}

export interface ServerCredentialSignerOptions {
  readonly keyId?: string;
  readonly ttlSeconds?: number;
  /** Returns a Unix epoch time in seconds. */
  readonly nowSeconds?: () => number;
  readonly createJti?: () => string;
}

export interface ServerAuthenticationRequest extends ServerProofRequest {
  readonly authorizationHeader?: string;
  readonly websocketProtocolHeader?: string | readonly string[];
}

export type ServerAuthenticationResult =
  | {
      readonly ok: true;
      readonly principal: ServerPrincipal;
      readonly websocketProtocol: string | null;
    }
  | {
      readonly ok: false;
      /** Authentication failures are deliberately indistinguishable to callers. */
      readonly status: 401 | 403;
    };

export interface ServerAuthenticatorOptions {
  readonly authToken?: string | null;
  readonly credentials?: readonly ServerCredentialConfig[];
  readonly replayCapacity?: number;
  /** Returns a Unix epoch time in seconds. */
  readonly nowSeconds?: () => number;
}

/**
 * Signs one short-lived, request-specific proof. The shared secret is never put
 * on the wire; callers must mint a new proof for every request and reconnect.
 */
export class ServerCredentialSigner implements ServerRequestSigner {
  readonly #keyId: string;
  readonly #secret: string;
  readonly #ttlSeconds: number;
  readonly #nowSeconds: () => number;
  readonly #createJti: () => string;

  constructor(secret: string, options: ServerCredentialSignerOptions = {}) {
    assertStrongServerAuthSecret(secret, SERVER_AUTH_TOKEN_ENV);
    this.#keyId = options.keyId ?? SERVER_AUTH_IMPLICIT_KEY_ID;
    assertServerKeyId(this.#keyId);
    this.#secret = secret;
    this.#ttlSeconds = options.ttlSeconds ?? SERVER_AUTH_DEFAULT_TTL_SECONDS;
    if (
      !Number.isInteger(this.#ttlSeconds)
      || this.#ttlSeconds < 1
      || this.#ttlSeconds > SERVER_AUTH_MAX_TTL_SECONDS
    ) {
      throw new Error(
        `server request proof TTL must be an integer from 1 to ${SERVER_AUTH_MAX_TTL_SECONDS} seconds`,
      );
    }
    this.#nowSeconds = options.nowSeconds ?? (() => Date.now() / 1000);
    this.#createJti = options.createJti ?? (() => randomBytes(16).toString("hex"));
  }

  signRequest(request: ServerProofRequest): string {
    const now = Math.floor(this.#nowSeconds());
    if (!Number.isSafeInteger(now)) throw new Error("server request proof clock is invalid");
    const jti = this.#createJti();
    assertServerJti(jti);
    const claims: ServerAuthClaims = {
      v: SERVER_AUTH_PROOF_VERSION,
      kid: this.#keyId,
      iat: now,
      exp: now + this.#ttlSeconds,
      jti,
      caps: normalizeCapabilities(request.capabilities),
      method: normalizeServerMethod(request.method),
      target: normalizeServerTarget(request.target),
      origin: request.origin ?? "",
      aud: request.audience ?? SERVER_AUTH_AUDIENCE,
    };
    const payload = Buffer.from(serializeCanonicalClaims(claims), "utf8").toString("base64url");
    const signingInput = `${AUTH_PROOF_PREFIX}.${payload}`;
    const signature = createHmac("sha256", this.#secret)
      .update(signingInput, "ascii")
      .digest("base64url");
    return `${signingInput}.${signature}`;
  }
}

/** A synchronous cache makes check-and-reserve atomic on Node's event loop. */
export class ServerReplayCache {
  readonly #capacity: number;
  readonly #entries = new Map<string, number>();

  constructor(capacity = SERVER_AUTH_DEFAULT_REPLAY_CAPACITY) {
    if (!Number.isSafeInteger(capacity) || capacity < 1) {
      throw new Error("server authentication replay capacity must be a positive integer");
    }
    this.#capacity = capacity;
  }

  reserve(keyId: string, jti: string, retainUntil: number, now: number): boolean {
    for (const [key, expiry] of this.#entries) {
      if (expiry < now) this.#entries.delete(key);
    }
    const cacheKey = `${keyId}\u0000${jti}`;
    if (this.#entries.has(cacheKey) || this.#entries.size >= this.#capacity) return false;
    this.#entries.set(cacheKey, retainUntil);
    return true;
  }

  get size(): number {
    return this.#entries.size;
  }
}

/** Authenticates proofs and returns the server-owned principal for authorization. */
export class ServerAuthenticator {
  readonly #credentials = new Map<string, ServerCredentialConfig>();
  readonly #replay: ServerReplayCache;
  readonly #nowSeconds: () => number;

  constructor(options: ServerAuthenticatorOptions = {}) {
    this.#nowSeconds = options.nowSeconds ?? (() => Date.now() / 1000);
    this.#replay = new ServerReplayCache(options.replayCapacity);
    const configuredToken = options.authToken ?? null;
    if (configuredToken !== null) {
      this.#addCredential({
        keyId: SERVER_AUTH_IMPLICIT_KEY_ID,
        secret: configuredToken,
        principalId: "host-operator",
        capabilities: SERVER_CAPABILITIES,
      });
    }
    for (const credential of options.credentials ?? []) this.#addCredential(credential);
  }

  get authenticationRequired(): boolean {
    return this.#credentials.size > 0;
  }

  authenticateRequest(request: ServerAuthenticationRequest): ServerAuthenticationResult {
    let requiredCapabilities: readonly ServerCapability[];
    let method: string;
    let target: string;
    try {
      requiredCapabilities = normalizeCapabilities(request.capabilities);
      method = normalizeServerMethod(request.method);
      target = normalizeServerTarget(request.target);
    } catch {
      return { ok: false, status: 401 };
    }
    const origin = request.origin ?? "";
    const audience = request.audience ?? SERVER_AUTH_AUDIENCE;

    if (!this.authenticationRequired) {
      return {
        ok: true,
        principal: {
          id: "insecure-loopback",
          keyId: "insecure-loopback",
          capabilities: new Set(SERVER_CAPABILITIES),
          authenticatedAt: Math.floor(this.#nowSeconds()),
          expiresAt: null,
          insecure: true,
        },
        websocketProtocol: null,
      };
    }

    const extracted = extractServerProof(
      request.authorizationHeader,
      request.websocketProtocolHeader,
    );
    if (extracted === null) return { ok: false, status: 401 };
    const parsed = parseServerProof(extracted.proof);
    if (parsed === null) return { ok: false, status: 401 };

    const credential = this.#credentials.get(parsed.claims.kid);
    const signingInput = `${AUTH_PROOF_PREFIX}.${parsed.payload}`;
    const expectedSignature = createHmac("sha256", credential?.secret ?? DUMMY_AUTH_SECRET)
      .update(signingInput, "ascii")
      .digest();
    if (
      parsed.signature.length !== expectedSignature.length
      || !timingSafeEqual(parsed.signature, expectedSignature)
      || credential === undefined
    ) {
      return { ok: false, status: 401 };
    }

    const now = Math.floor(this.#nowSeconds());
    const claims = parsed.claims;
    if (
      credential.disabled === true
      || (credential.notBefore !== undefined && now < credential.notBefore)
      || (credential.notAfter !== undefined && now > credential.notAfter)
      || claims.iat > now + SERVER_AUTH_CLOCK_SKEW_SECONDS
      || claims.exp < now - SERVER_AUTH_CLOCK_SKEW_SECONDS
      || claims.exp <= claims.iat
      || claims.exp - claims.iat > SERVER_AUTH_MAX_TTL_SECONDS
      || claims.method !== method
      || claims.target !== target
      || claims.origin !== origin
      || claims.aud !== audience
    ) {
      return { ok: false, status: 401 };
    }

    const configuredCapabilities = expandCapabilities(credential.capabilities);
    const requestedCapabilities = expandCapabilities(claims.caps);
    if (
      claims.caps.some((capability) => !configuredCapabilities.has(capability))
      || requiredCapabilities.some((capability) => !requestedCapabilities.has(capability))
    ) {
      return { ok: false, status: 403 };
    }
    if (
      !this.#replay.reserve(
        claims.kid,
        claims.jti,
        claims.exp + SERVER_AUTH_CLOCK_SKEW_SECONDS,
        now,
      )
    ) {
      return { ok: false, status: 401 };
    }

    return {
      ok: true,
      principal: {
        id: credential.principalId,
        keyId: credential.keyId,
        capabilities: requestedCapabilities,
        authenticatedAt: now,
        expiresAt: Math.min(
          claims.exp,
          credential.notAfter === undefined ? claims.exp : credential.notAfter + 1,
        ),
        insecure: false,
      },
      websocketProtocol: extracted.websocketProtocol,
    };
  }

  principalHasCapabilities(
    principal: ServerPrincipal,
    required: readonly ServerCapability[],
  ): boolean {
    const capabilities = normalizeCapabilities(required);
    if (principal.insecure) {
      return capabilities.every((capability) => principal.capabilities.has(capability));
    }
    const credential = this.#credentials.get(principal.keyId);
    const now = Math.floor(this.#nowSeconds());
    if (
      credential === undefined
      || credential.disabled === true
      || (principal.expiresAt !== null && now >= principal.expiresAt)
      || (credential.notBefore !== undefined && now < credential.notBefore)
      || (credential.notAfter !== undefined && now > credential.notAfter)
    ) {
      return false;
    }
    return capabilities.every((capability) => principal.capabilities.has(capability));
  }

  #addCredential(credential: ServerCredentialConfig): void {
    assertServerKeyId(credential.keyId);
    assertStrongServerAuthSecret(credential.secret, `server credential ${credential.keyId}`);
    if (
      credential.principalId.length === 0
      || credential.principalId.length > 128
      || [...credential.principalId].some((character) => character.charCodeAt(0) < 32)
    ) {
      throw new Error(`server credential ${credential.keyId} has an invalid principalId`);
    }
    const capabilities = normalizeCapabilities(credential.capabilities);
    if (capabilities.length === 0) {
      throw new Error(`server credential ${credential.keyId} must grant at least one capability`);
    }
    if (this.#credentials.has(credential.keyId)) {
      throw new Error(`duplicate server credential keyId: ${credential.keyId}`);
    }
    if (
      credential.notBefore !== undefined
      && !Number.isSafeInteger(credential.notBefore)
    ) {
      throw new Error(`server credential ${credential.keyId} has an invalid notBefore`);
    }
    if (
      credential.notAfter !== undefined
      && (!Number.isSafeInteger(credential.notAfter)
        || credential.notAfter >= Number.MAX_SAFE_INTEGER
        || (credential.notBefore !== undefined && credential.notAfter <= credential.notBefore))
    ) {
      throw new Error(`server credential ${credential.keyId} has an invalid notAfter`);
    }
    this.#credentials.set(credential.keyId, { ...credential, capabilities });
  }
}

export function resolveServerAuthToken(explicitToken?: string): string | null {
  const token = explicitToken ?? process.env[SERVER_AUTH_TOKEN_ENV];
  if (token === undefined || token === "") return null;
  assertStrongServerAuthSecret(token, SERVER_AUTH_TOKEN_ENV);
  return token;
}

/** Load the cross-runtime v1 key registry from its secure on-disk envelope. */
export function resolveServerCredentialsFile(
  explicitPath?: string | null,
): readonly ServerCredentialConfig[] {
  const path = explicitPath ?? process.env[SERVER_CREDENTIALS_FILE_ENV];
  if (path === undefined || path === "") return [];
  if (process.platform === "win32") {
    throw new Error(
      `${SERVER_CREDENTIALS_FILE_ENV} is unsupported on Windows until owner and DACL validation is available; `
      + `use ${SERVER_AUTH_TOKEN_ENV} instead`,
    );
  }
  if (!isAbsolute(path) || parse(path).root === "" || path.split(/[\\/]/).includes("..")) {
    throw new Error(
      `${SERVER_CREDENTIALS_FILE_ENV} must name an absolute path without '..'`,
    );
  }
  rejectSymlinkComponents(path);

  let descriptor: number;
  try {
    const noFollow = optionalFsConstant("O_NOFOLLOW");
    const closeOnExec = optionalFsConstant("O_CLOEXEC");
    descriptor = openSync(path, fsConstants.O_RDONLY | noFollow | closeOnExec);
  } catch (error) {
    throw new Error(
      `cannot securely open ${SERVER_CREDENTIALS_FILE_ENV}: ${errorMessage(error)}`,
    );
  }

  let raw: Buffer;
  try {
    const before = fstatSync(descriptor);
    validateCredentialsFileMetadata(path, before);
    const pathnameMetadata = lstatSync(path);
    if (pathnameMetadata.dev !== before.dev || pathnameMetadata.ino !== before.ino) {
      throw new Error("credentials registry path changed while it was being opened");
    }
    const buffer = Buffer.alloc(MAX_SERVER_CREDENTIALS_FILE_BYTES + 1);
    let offset = 0;
    for (;;) {
      const read = readSync(descriptor, buffer, offset, buffer.length - offset, null);
      if (read === 0) break;
      offset += read;
      if (offset === buffer.length) break;
    }
    if (offset > MAX_SERVER_CREDENTIALS_FILE_BYTES) {
      throw new Error("credentials registry exceeds size limit");
    }
    const after = fstatSync(descriptor);
    if (
      before.dev !== after.dev
      || before.ino !== after.ino
      || before.size !== after.size
      || before.mtimeMs !== after.mtimeMs
    ) {
      throw new Error("credentials registry changed while it was being read");
    }
    raw = buffer.subarray(0, offset);
  } finally {
    closeSync(descriptor);
  }

  let document: unknown;
  try {
    const text = new TextDecoder("utf-8", { fatal: true }).decode(raw);
    document = JSON.parse(text);
  } catch (error) {
    throw new Error(`credentials registry is not valid UTF-8 JSON: ${errorMessage(error)}`);
  }
  if (!isRecord(document) || !hasExactKeys(document, ["credentials", "version"])) {
    throw new Error("credentials registry must contain only version and credentials");
  }
  if (document.version !== 1) {
    throw new Error("unsupported credentials registry version");
  }
  if (!Array.isArray(document.credentials) || document.credentials.length > MAX_SERVER_CREDENTIALS) {
    throw new Error(
      `credentials registry may contain at most ${MAX_SERVER_CREDENTIALS} entries`,
    );
  }
  return document.credentials.map((entry, index) => parseFileCredential(entry, index));
}

/**
 * Resolve the exact browser origins allowed to use the control plane through a
 * reverse proxy. These origins supplement the server's loopback-origin rules;
 * they do not change the bind address or authentication requirements.
 */
export function resolveServerAllowedOrigins(
  explicitOrigins?: readonly string[],
): ReadonlySet<string> {
  const configured = explicitOrigins ?? parseAllowedOriginsEnv(
    process.env[SERVER_ALLOWED_ORIGINS_ENV],
  );
  const origins = new Set<string>();
  for (const value of configured) {
    origins.add(normalizeServerBrowserOrigin(value));
  }
  return origins;
}

/** Return whether a browser-supplied Origin exactly matches the allowlist. */
export function isExplicitlyAllowedServerOrigin(
  origin: string,
  allowedOrigins: ReadonlySet<string>,
): boolean {
  try {
    return allowedOrigins.has(normalizeServerBrowserOrigin(origin));
  } catch {
    return false;
  }
}

/** Reverse-proxy browser origins are never compatible with tokenless mode. */
export function assertServerAllowedOriginsRequireAuthentication(
  authenticationRequired: boolean,
  allowedOrigins: ReadonlySet<string>,
): void {
  if (!authenticationRequired && allowedOrigins.size > 0) {
    throw new Error(
      "Configured browser origins require control-plane credentials; "
        + "tokenless loopback mode must not be placed behind a reverse proxy.",
    );
  }
}

export function assertSecureServerBind(
  host: string,
  authToken: string | null,
  allowTokenlessLoopback = process.env[SERVER_ALLOW_TOKENLESS_LOOPBACK_ENV] === "1",
): void {
  if (authToken !== null) return;
  if (isLoopbackHost(host) && allowTokenlessLoopback) return;
  throw new Error(
    `Refusing to bind the unauthenticated control plane to host ${JSON.stringify(host)}. `
      + `Set ${SERVER_CREDENTIALS_FILE_ENV}, or set ${SERVER_AUTH_TOKEN_ENV} to a random HMAC key `
      + `of at least ${MIN_SERVER_AUTH_TOKEN_LENGTH} bytes. For an explicitly tokenless loopback server, `
      + `set ${SERVER_ALLOW_TOKENLESS_LOOPBACK_ENV}=1.`,
  );
}

/** Compatibility helper for callers that do not need to retain replay state. */
export function isServerRequestAuthorized(input: {
  authToken: string | null;
  authorizationHeader?: string;
  websocketProtocolHeader?: string | readonly string[];
  method?: string;
  target?: string;
  origin?: string;
  capabilities?: readonly ServerCapability[];
}): boolean {
  void input;
  // A per-call verifier cannot preserve one-time replay state. Retain this
  // legacy helper only as a fail-closed source-compatibility shim.
  return false;
}

export function serverAuthorizationHeader(
  secret: string,
  request: ServerProofRequest,
  options: ServerCredentialSignerOptions = {},
): string {
  return `Bearer ${new ServerCredentialSigner(secret, options).signRequest(request)}`;
}

export function serverAuthSubprotocol(
  secret: string,
  request: ServerProofRequest = {
    method: "GET",
    target: "/ws/interactive",
    capabilities: ["content:read", "control:operate", "host:execute"],
  },
  options: ServerCredentialSignerOptions = {},
): string {
  return serverAuthSubprotocolFromProof(
    new ServerCredentialSigner(secret, options).signRequest(request),
  );
}

export function serverAuthSubprotocolFromProof(proof: string): string {
  if (parseServerProof(proof) === null) {
    throw new Error("invalid server authentication proof");
  }
  return proof;
}

/** Select a syntactically valid auth protocol; authentication happens before upgrade. */
export function selectServerAuthSubprotocol(
  protocols: Iterable<string>,
  _authToken?: string | null,
): string | null {
  for (const protocol of protocols) {
    if (decodeServerAuthSubprotocol(protocol) !== null) return protocol;
  }
  return null;
}

export function requiredServerHttpCapabilities(
  method: string,
  target: string,
): readonly ServerCapability[] {
  const normalizedMethod = normalizeServerMethod(method);
  const pathname = normalizeServerTarget(target).split("?", 1)[0]!;
  const readOnly = normalizedMethod === "GET" || normalizedMethod === "HEAD";
  const capabilities: ServerCapability[] = [readOnly ? "control:read" : "control:operate"];
  if (pathname.startsWith("/api/")) capabilities.push("content:read");
  if (
    (!readOnly && serverHttpRouteExecutesHost(pathname))
    || (readOnly && serverHttpReadRouteExecutesHost(pathname))
  ) {
    capabilities.push("host:execute");
  }
  return capabilities;
}

export function requiredInteractiveMessageCapabilities(
  messageType: string,
): readonly ServerCapability[] {
  switch (messageType) {
    case "chat_agent":
    case "start_run":
    case "create_scenario":
    case "create_task":
    case "confirm_scenario":
    case "revise_scenario":
    case "resume":
    case "override_gate":
      return ["control:operate", "host:execute"];
    case "login":
    case "logout":
    case "switch_provider":
      return ["control:admin"];
    default:
      return ["control:operate"];
  }
}

function serverHttpRouteExecutesHost(pathname: string): boolean {
  return pathname === "/api/knowledge/solve"
    || pathname === "/api/knowledge/import"
    || /^\/api\/hub\/(?:packages|results)\/from-run\/[^/]+$/.test(pathname)
    || /^\/api\/hub\/packages\/[^/]+\/adopt$/.test(pathname)
    || /^\/api\/cockpit\/runs\/[^/]+\/consult$/.test(pathname)
    || /^\/api\/openclaw\/artifacts\/?$/.test(pathname)
    || /^\/api\/openclaw\/(?:evaluate|validate|distill)(?:\/|$)/.test(pathname)
    || /^\/api\/simulations(?:\/|$)/.test(pathname)
    || /^\/api\/missions\/[^/]+\/(?:run|resume)$/.test(pathname)
    || /^\/api\/campaigns\/[^/]+\/resume$/.test(pathname);
}

function serverHttpReadRouteExecutesHost(pathname: string): boolean {
  return /^\/api\/knowledge\/solve\/[^/]+$/.test(pathname)
    || pathname === "/api/openclaw/discovery/capabilities"
    || pathname === "/api/openclaw/skill/manifest"
    || /^\/api\/openclaw\/discovery\/scenario\/[^/]+$/.test(pathname);
}

export function isLoopbackHost(host: string): boolean {
  const normalized = host.toLowerCase().replace(/^\[|\]$/g, "");
  if (
    normalized === "localhost"
    || normalized.endsWith(".localhost")
    || normalized === "::1"
    || normalized === "0:0:0:0:0:0:0:1"
  ) {
    return true;
  }
  const octets = normalized.split(".");
  return (
    octets.length === 4
    && octets.every((octet) => /^\d{1,3}$/.test(octet) && Number(octet) <= 255)
    && Number(octets[0]) === 127
  );
}

function extractServerProof(
  authorizationHeader: string | undefined,
  websocketProtocolHeader: string | readonly string[] | undefined,
): { proof: string; websocketProtocol: string | null } | null {
  const headerProof = readBearerProof(authorizationHeader);
  const protocolProofs = readProtocolHeader(websocketProtocolHeader)
    .map((protocol) => ({ protocol, proof: decodeServerAuthSubprotocol(protocol) }))
    .filter((entry): entry is { protocol: string; proof: string } => entry.proof !== null);
  if (headerProof !== null && protocolProofs.length > 0) return null;
  if (protocolProofs.length > 1) return null;
  if (headerProof !== null) return { proof: headerProof, websocketProtocol: null };
  const protocol = protocolProofs[0];
  return protocol ? { proof: protocol.proof, websocketProtocol: protocol.protocol } : null;
}

function parseServerProof(
  proof: string,
): { claims: ServerAuthClaims; payload: string; signature: Buffer } | null {
  const parts = proof.split(".");
  if (parts.length !== 3 || parts[0] !== AUTH_PROOF_PREFIX) return null;
  const payloadBytes = decodeCanonicalBase64Url(parts[1]!);
  const signature = decodeCanonicalBase64Url(parts[2]!);
  if (payloadBytes === null || signature === null || signature.length !== 32) return null;
  let decoded: unknown;
  try {
    decoded = JSON.parse(payloadBytes.toString("utf8"));
  } catch {
    return null;
  }
  if (!isRecord(decoded)) return null;
  const keys = Object.keys(decoded).sort();
  if (keys.length !== AUTH_PROOF_CLAIMS.length) return null;
  if (!keys.every((key, index) => key === AUTH_PROOF_CLAIMS[index])) return null;
  if (
    decoded.v !== SERVER_AUTH_PROOF_VERSION
    || typeof decoded.kid !== "string"
    || typeof decoded.iat !== "number"
    || typeof decoded.exp !== "number"
    || typeof decoded.jti !== "string"
    || !Array.isArray(decoded.caps)
    || typeof decoded.method !== "string"
    || typeof decoded.target !== "string"
    || typeof decoded.origin !== "string"
    || typeof decoded.aud !== "string"
    || !Number.isSafeInteger(decoded.iat)
    || !Number.isSafeInteger(decoded.exp)
  ) {
    return null;
  }
  try {
    assertServerKeyId(decoded.kid);
    assertServerJti(decoded.jti);
    const caps = validateCanonicalCapabilities(decoded.caps);
    if (normalizeServerMethod(decoded.method) !== decoded.method) return null;
    if (normalizeServerTarget(decoded.target) !== decoded.target) return null;
    const claims: ServerAuthClaims = {
      v: SERVER_AUTH_PROOF_VERSION,
      kid: decoded.kid,
      iat: decoded.iat,
      exp: decoded.exp,
      jti: decoded.jti,
      caps,
      method: decoded.method,
      target: decoded.target,
      origin: decoded.origin,
      aud: decoded.aud,
    };
    if (
      decoded.origin.length > 8_192
      || decoded.aud !== SERVER_AUTH_AUDIENCE
      || Buffer.from(serializeCanonicalClaims(claims), "utf8").toString("base64url")
        !== parts[1]
    ) {
      return null;
    }
    return {
      claims,
      payload: parts[1]!,
      signature,
    };
  } catch {
    return null;
  }
}

function readBearerProof(value: string | undefined): string | null {
  if (!value) return null;
  const match = /^Bearer ([^\s]+)$/.exec(value);
  return match?.[1] ?? null;
}

function parseAllowedOriginsEnv(value: string | undefined): string[] {
  if (value === undefined || value.trim() === "") return [];
  const origins = value.split(",").map((origin) => origin.trim());
  if (origins.some((origin) => origin === "")) {
    throw new Error(
      `${SERVER_ALLOWED_ORIGINS_ENV} must be a comma-separated list of browser origins`,
    );
  }
  return origins;
}

function normalizeServerBrowserOrigin(value: string): string {
  const trimmed = value.trim();
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    throw new Error(
      `${SERVER_ALLOWED_ORIGINS_ENV} contains an invalid browser origin: ${JSON.stringify(value)}`,
    );
  }
  if (
    (parsed.protocol !== "http:" && parsed.protocol !== "https:")
    || parsed.username !== ""
    || parsed.password !== ""
    || parsed.pathname !== "/"
    || parsed.search !== ""
    || parsed.hash !== ""
    || parsed.origin === "null"
    || parsed.hostname.includes("*")
  ) {
    throw new Error(
      `${SERVER_ALLOWED_ORIGINS_ENV} entries must be exact http(s) browser origins without credentials, wildcards, paths, queries, or fragments: ${JSON.stringify(value)}`,
    );
  }
  return parsed.origin;
}

function readProtocolHeader(value: string | readonly string[] | undefined): string[] {
  if (value === undefined) return [];
  return (typeof value === "string" ? [value] : value)
    .flatMap((header) => header.split(","))
    .map((protocol) => protocol.trim())
    .filter(Boolean);
}

function decodeServerAuthSubprotocol(protocol: string): string | null {
  if (!protocol.startsWith(SERVER_AUTH_SUBPROTOCOL_PREFIX)) return null;
  return parseServerProof(protocol) === null ? null : protocol;
}

function decodeCanonicalBase64Url(value: string): Buffer | null {
  if (!/^[A-Za-z0-9_-]+$/.test(value)) return null;
  const bytes = Buffer.from(value, "base64url");
  return bytes.toString("base64url") === value ? bytes : null;
}

function assertStrongServerAuthSecret(secret: string, source: string): void {
  const length = Buffer.byteLength(secret, "utf8");
  if (length < MIN_SERVER_AUTH_TOKEN_LENGTH) {
    throw new Error(
      `${source} must contain at least ${MIN_SERVER_AUTH_TOKEN_LENGTH} bytes`,
    );
  }
  if (length > MAX_SERVER_AUTH_SECRET_BYTES) throw new Error(`${source} is too large`);
}

function assertServerKeyId(keyId: string): void {
  if (!/^[A-Za-z0-9._-]{1,64}$/.test(keyId)) {
    throw new Error("server credential keyId must use 1-64 URL-safe characters");
  }
}

function assertServerJti(jti: string): void {
  if (!/^[0-9a-f]{32}$/.test(jti)) {
    throw new Error("server request proof jti must be 32 lowercase hexadecimal characters");
  }
}

function normalizeServerMethod(method: string): string {
  const normalized = method.toUpperCase();
  if (!/^[A-Z]+$/.test(normalized)) throw new Error("server request method is invalid");
  return normalized;
}

function normalizeServerTarget(target: string): string {
  if (!target.startsWith("/") || target.includes("#") || target.length > 8_192) {
    throw new Error("server request target must be a bounded raw path and query string");
  }
  const parsed = new URL(target, "http://localhost");
  if (
    parsed.origin !== "http://localhost"
    || `${parsed.pathname}${parsed.search}` !== target
  ) {
    throw new Error("server request target must not contain normalized path segments");
  }
  return target;
}

function normalizeCapabilities(
  capabilities: readonly ServerCapability[],
): readonly ServerCapability[] {
  const normalized = [...new Set(capabilities)].sort();
  if (normalized.length === 0) {
    throw new Error("server request must contain at least one capability");
  }
  if (normalized.some((capability) => !SERVER_CAPABILITY_SET.has(capability))) {
    throw new Error("server request contains an unknown capability");
  }
  return normalized;
}

function validateCanonicalCapabilities(value: readonly unknown[]): readonly ServerCapability[] {
  const capabilities: string[] = [];
  for (const capability of value) {
    if (typeof capability !== "string") {
      throw new Error("server request proof capabilities must be strings");
    }
    capabilities.push(capability);
  }
  const sorted = [...new Set(capabilities)].sort();
  if (
    sorted.length === 0
    || sorted.length !== capabilities.length
    || sorted.some((capability, index) => capability !== capabilities[index])
  ) {
    throw new Error("server request proof capabilities must be sorted, unique, and known");
  }
  const normalized: ServerCapability[] = [];
  for (const capability of sorted) {
    if (!isServerCapability(capability)) {
      throw new Error("server request proof capabilities must be sorted, unique, and known");
    }
    normalized.push(capability);
  }
  return normalized;
}

function expandCapabilities(
  capabilities: readonly ServerCapability[],
): ReadonlySet<ServerCapability> {
  return capabilities.includes("control:admin")
    ? new Set<ServerCapability>([...capabilities, "control:operate", "control:read"])
    : new Set<ServerCapability>(capabilities);
}

function serializeCanonicalClaims(claims: ServerAuthClaims): string {
  // Match Python json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False).
  const ordered = {
    aud: claims.aud,
    caps: claims.caps,
    exp: claims.exp,
    iat: claims.iat,
    jti: claims.jti,
    kid: claims.kid,
    method: claims.method,
    origin: claims.origin,
    target: claims.target,
    v: claims.v,
  };
  return JSON.stringify(ordered);
}

function isServerCapability(value: string): value is ServerCapability {
  return SERVER_CAPABILITY_SET.has(value);
}

function optionalFsConstant(name: string): number {
  const value: unknown = Reflect.get(fsConstants, name);
  return typeof value === "number" ? value : 0;
}

function rejectSymlinkComponents(path: string): void {
  const root = parse(path).root;
  const components = relative(root, path).split(sep).filter(Boolean);
  let current = root;
  for (const [index, component] of components.entries()) {
    current = join(current, component);
    try {
      const metadata = lstatSync(current);
      if (metadata.isSymbolicLink()) {
        throw new Error(`credentials registry path contains symlink ${current}`);
      }
      if (index < components.length - 1) {
        if (!metadata.isDirectory()) {
          throw new Error(`credentials registry parent is not a directory: ${current}`);
        }
        if (process.platform !== "win32") {
          if ((metadata.mode & 0o022) !== 0) {
            throw new Error(`credentials registry parent is group/world writable: ${current}`);
          }
          if (
            typeof process.getuid === "function"
            && metadata.uid !== 0
            && metadata.uid !== process.getuid()
          ) {
            throw new Error(`credentials registry parent has an untrusted owner: ${current}`);
          }
        }
      }
    } catch (error) {
      if (isNodeError(error) && error.code === "ENOENT") return;
      throw error;
    }
  }
}

function validateCredentialsFileMetadata(path: string, metadata: Stats): void {
  if (!metadata.isFile()) {
    throw new Error(`credentials registry ${path} is not a regular file`);
  }
  if (metadata.size > MAX_SERVER_CREDENTIALS_FILE_BYTES) {
    throw new Error("credentials registry exceeds size limit");
  }
  if (process.platform !== "win32") {
    if (typeof process.getuid === "function" && metadata.uid !== process.getuid()) {
      throw new Error("credentials registry must be owned by the current user");
    }
    const mode = metadata.mode & 0o777;
    if (mode !== 0o400 && mode !== 0o600) {
      throw new Error("credentials registry permissions must be 0400 or 0600");
    }
  }
}

function parseFileCredential(value: unknown, index: number): ServerCredentialConfig {
  if (!isRecord(value)) {
    throw new Error(`credentials registry entry ${index} must be an object`);
  }
  const required = ["capabilities", "kid", "principal", "secret"];
  const allowed = new Set([...required, "disabled", "not_after", "not_before"]);
  const keys = Object.keys(value);
  if (
    required.some((field) => !Object.hasOwn(value, field))
    || keys.some((field) => !allowed.has(field))
  ) {
    throw new Error("credentials registry entry has missing or unexpected fields");
  }
  if (
    typeof value.kid !== "string"
    || typeof value.principal !== "string"
    || typeof value.secret !== "string"
  ) {
    throw new Error("credential kid, principal, and secret must be strings");
  }
  if (!Array.isArray(value.capabilities)) {
    throw new Error("credential capabilities must be a sorted unique non-empty array");
  }
  const capabilities = validateCanonicalCapabilities(value.capabilities);
  const notBefore = value.not_before;
  const notAfter = value.not_after;
  if (notBefore !== undefined && (typeof notBefore !== "number" || !Number.isSafeInteger(notBefore))) {
    throw new Error("credential not_before must be an integer");
  }
  if (notAfter !== undefined && (typeof notAfter !== "number" || !Number.isSafeInteger(notAfter))) {
    throw new Error("credential not_after must be an integer");
  }
  if (value.disabled !== undefined && typeof value.disabled !== "boolean") {
    throw new Error("credential disabled must be a boolean");
  }
  return {
    keyId: value.kid,
    principalId: value.principal,
    secret: value.secret,
    capabilities,
    ...(notBefore === undefined ? {} : { notBefore }),
    ...(notAfter === undefined ? {} : { notAfter }),
    ...(value.disabled === undefined ? {} : { disabled: value.disabled }),
  };
}

function hasExactKeys(value: Record<string, unknown>, expected: readonly string[]): boolean {
  const keys = Object.keys(value).sort();
  return keys.length === expected.length && keys.every((key, index) => key === expected[index]);
}

function isNodeError(value: unknown): value is NodeJS.ErrnoException {
  return value instanceof Error && "code" in value;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
