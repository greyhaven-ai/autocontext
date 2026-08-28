import { timingSafeEqual } from "node:crypto";

export const SERVER_AUTH_TOKEN_ENV = "AUTOCONTEXT_SERVER_TOKEN";
export const SERVER_ALLOWED_ORIGINS_ENV = "AUTOCONTEXT_SERVER_ALLOWED_ORIGINS";
export const SERVER_AUTH_SUBPROTOCOL_PREFIX = "autocontext.bearer.";

const MIN_SERVER_AUTH_TOKEN_LENGTH = 32;

export function resolveServerAuthToken(explicitToken?: string): string | null {
  const token = explicitToken ?? process.env[SERVER_AUTH_TOKEN_ENV];
  if (token === undefined || token === "") return null;
  if (token.length < MIN_SERVER_AUTH_TOKEN_LENGTH) {
    throw new Error(
      `${SERVER_AUTH_TOKEN_ENV} must contain at least ${MIN_SERVER_AUTH_TOKEN_LENGTH} characters`,
    );
  }
  return token;
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

export function assertSecureServerBind(host: string, authToken: string | null): void {
  if (isLoopbackHost(host) || authToken !== null) return;
  throw new Error(
    `Refusing to bind the unauthenticated control plane to non-loopback host ${JSON.stringify(host)}. ` +
      `Set ${SERVER_AUTH_TOKEN_ENV} to a random value of at least ${MIN_SERVER_AUTH_TOKEN_LENGTH} characters.`,
  );
}

export function isServerRequestAuthorized(input: {
  authToken: string | null;
  authorizationHeader?: string;
  websocketProtocolHeader?: string | readonly string[];
}): boolean {
  if (input.authToken === null) return true;

  const headerToken = readBearerToken(input.authorizationHeader);
  if (headerToken !== null && constantTimeEqual(headerToken, input.authToken)) return true;
  return selectServerAuthSubprotocol(
    readProtocolHeader(input.websocketProtocolHeader),
    input.authToken,
  ) !== null;
}

export function serverAuthSubprotocol(token: string): string {
  return `${SERVER_AUTH_SUBPROTOCOL_PREFIX}${Buffer.from(token, "utf8").toString("base64url")}`;
}

export function selectServerAuthSubprotocol(
  protocols: Iterable<string>,
  authToken: string | null,
): string | null {
  if (authToken === null) return null;
  for (const protocol of protocols) {
    const candidate = decodeServerAuthSubprotocol(protocol);
    if (candidate !== null && constantTimeEqual(candidate, authToken)) return protocol;
  }
  return null;
}

export function isLoopbackHost(host: string): boolean {
  const normalized = host.toLowerCase().replace(/^\[|\]$/g, "");
  if (
    normalized === "localhost" ||
    normalized.endsWith(".localhost") ||
    normalized === "::1" ||
    normalized === "0:0:0:0:0:0:0:1"
  ) {
    return true;
  }
  const octets = normalized.split(".");
  return (
    octets.length === 4 &&
    octets.every((octet) => /^\d{1,3}$/.test(octet) && Number(octet) <= 255) &&
    Number(octets[0]) === 127
  );
}

function readBearerToken(value: string | undefined): string | null {
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
  const encoded = protocol.slice(SERVER_AUTH_SUBPROTOCOL_PREFIX.length);
  if (!/^[A-Za-z0-9_-]+$/.test(encoded)) return null;
  const bytes = Buffer.from(encoded, "base64url");
  if (bytes.toString("base64url") !== encoded) return null;
  const candidate = bytes.toString("utf8");
  if (!Buffer.from(candidate, "utf8").equals(bytes)) return null;
  return candidate;
}

function constantTimeEqual(left: string, right: string): boolean {
  const leftBytes = Buffer.from(left, "utf8");
  const rightBytes = Buffer.from(right, "utf8");
  if (leftBytes.length !== rightBytes.length) return false;
  return timingSafeEqual(leftBytes, rightBytes);
}
