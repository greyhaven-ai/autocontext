import { ulid } from "ulid";

declare const brand: unique symbol;
type Brand<T, B> = T & { readonly [brand]: B };

// Branded IDs introduced by the production-traces contract.
export type ProductionTraceId = Brand<string, "ProductionTraceId">;
export type AppId = Brand<string, "AppId">;
export type UserIdHash = Brand<string, "UserIdHash">;
export type SessionIdHash = Brand<string, "SessionIdHash">;
export type FeedbackRefId = Brand<string, "FeedbackRefId">;
export type EnvironmentTag = Brand<string, "EnvironmentTag">;
export type ContentHash = Brand<string, "ContentHash">;
export type Scenario = Brand<string, "Scenario">;

// Crockford base32: 0-9 A-H J K M N P-T V-Z (excludes I L O U). ULID is 26 chars.
const ULID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/;
// AppId and Scenario: lowercase alnum start + [a-z0-9_-]* — path-safe and grep-friendly.
const SLUG_RE = /^[a-z0-9][a-z0-9_-]*$/;
// EnvironmentTag: slightly more permissive (allows tenant prefixes) but still path-safe.
const ENV_TAG_RE = /^[a-z0-9][a-z0-9_-]*$/i;
// SHA-256 hex — 64 chars, lowercase.
const SHA256_HEX_RE = /^[0-9a-f]{64}$/;
// sha256:<64 lowercase hex>.
const CONTENT_HASH_RE = /^sha256:[0-9a-f]{64}$/;

export function newProductionTraceId(): ProductionTraceId {
	return ulid() as ProductionTraceId;
}

export function parseProductionTraceId(
	input: string,
): ProductionTraceId | null {
	return ULID_RE.test(input) ? (input as ProductionTraceId) : null;
}

export function parseAppId(input: string): AppId | null {
	if (input === ".." || input.includes("/") || input.includes("\\"))
		return null;
	return SLUG_RE.test(input) ? (input as AppId) : null;
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

export function parseEnvironmentTag(input: string): EnvironmentTag | null {
	if (input === ".." || input.includes("/") || input.includes("\\"))
		return null;
	return ENV_TAG_RE.test(input) ? (input as EnvironmentTag) : null;
}

export function defaultEnvironmentTag(): EnvironmentTag {
	return "production" as EnvironmentTag;
}

export function parseContentHash(input: string): ContentHash | null {
	return CONTENT_HASH_RE.test(input) ? (input as ContentHash) : null;
}

export function parseScenario(input: string): Scenario | null {
	return SLUG_RE.test(input) ? (input as Scenario) : null;
}
