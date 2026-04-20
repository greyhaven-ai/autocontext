import { ulid } from "ulid";

declare const brand: unique symbol;
type Brand<T, B> = T & { readonly [brand]: B };

// Branded IDs introduced by the production-traces contract.
export type ProductionTraceId = Brand<string, "ProductionTraceId">;
export type AppId             = Brand<string, "AppId">;
export type UserIdHash        = Brand<string, "UserIdHash">;
export type SessionIdHash     = Brand<string, "SessionIdHash">;
export type FeedbackRefId     = Brand<string, "FeedbackRefId">;

// Crockford base32: 0-9 A-H J K M N P-T V-Z (excludes I L O U). ULID is 26 chars.
const ULID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/;
// AppId: lowercase alnum start + [a-z0-9_-]* — path-safe and grep-friendly.
const APP_ID_RE = /^[a-z0-9][a-z0-9_-]*$/;
// SHA-256 hex — 64 chars, lowercase.
const SHA256_HEX_RE = /^[0-9a-f]{64}$/;

export function newProductionTraceId(): ProductionTraceId {
  return ulid() as ProductionTraceId;
}

export function parseProductionTraceId(input: string): ProductionTraceId | null {
  return ULID_RE.test(input) ? (input as ProductionTraceId) : null;
}

export function parseAppId(input: string): AppId | null {
  if (input === ".." || input.includes("/") || input.includes("\\")) return null;
  return APP_ID_RE.test(input) ? (input as AppId) : null;
}

export function parseUserIdHash(input: string): UserIdHash | null {
  return SHA256_HEX_RE.test(input) ? (input as UserIdHash) : null;
}

export function parseSessionIdHash(input: string): SessionIdHash | null {
  return SHA256_HEX_RE.test(input) ? (input as SessionIdHash) : null;
}

export function parseFeedbackRefId(input: string): FeedbackRefId | null {
  // Opaque customer-supplied identifier: reject only if fully whitespace or empty.
  if (input.trim().length === 0) return null;
  return input as FeedbackRefId;
}

// Re-exports from control-plane/contract/ for ergonomic downstream imports.
// Do NOT duplicate — downstream consumers should import these brands from here
// so production-traces stays the single import origin for this contract.
export {
  parseEnvironmentTag,
  defaultEnvironmentTag,
  parseContentHash,
  parseScenario,
} from "../../control-plane/contract/branded-ids.js";
export type {
  EnvironmentTag,
  ContentHash,
  Scenario,
} from "../../control-plane/contract/branded-ids.js";
