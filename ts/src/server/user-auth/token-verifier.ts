import {
  createLocalJWKSet,
  createRemoteJWKSet,
  jwtVerify,
  type JSONWebKeySet,
  type JWTPayload,
} from "jose";
import type { UserAuthConfig } from "./config.js";

export interface VerifiedIdentity {
  subject: string;
  email?: string;
  groups: string[];
}

export interface TokenVerifier {
  verify(token: string): Promise<VerifiedIdentity>;
}

/** Pure claim extraction: groups from `groups`, else `roles`, else []. */
export function identityFromClaims(payload: JWTPayload): VerifiedIdentity {
  const raw = (payload.groups ?? payload.roles) as unknown;
  const groups = Array.isArray(raw) ? raw.map(String) : [];
  return {
    subject: String(payload.sub ?? ""),
    email: typeof payload.email === "string" ? payload.email : undefined,
    groups,
  };
}

type KeyResolver = ReturnType<typeof createLocalJWKSet>;

function verifierFromResolver(
  keys: KeyResolver,
  opts: { issuer: string; audience?: string },
): TokenVerifier {
  return {
    async verify(token: string): Promise<VerifiedIdentity> {
      const { payload } = await jwtVerify(token, keys, {
        issuer: opts.issuer,
        audience: opts.audience,
      });
      return identityFromClaims(payload);
    },
  };
}

/** Verifier backed by a remote JWKS endpoint (production). */
export function createJwksVerifier(config: UserAuthConfig): TokenVerifier {
  const jwks = createRemoteJWKSet(new URL(config.jwksUrl)) as KeyResolver;
  return verifierFromResolver(jwks, { issuer: config.issuer, audience: config.audience });
}

/** Verifier backed by an in-memory JWKS (tests / injected). */
export function createVerifierWithKeySet(
  jwks: JSONWebKeySet,
  opts: { issuer: string; audience?: string },
): TokenVerifier {
  return verifierFromResolver(createLocalJWKSet(jwks), opts);
}
