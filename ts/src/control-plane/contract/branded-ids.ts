import { ulid } from "ulid";

declare const brand: unique symbol;
type Brand<T, B> = T & { readonly [brand]: B };

export type ArtifactId     = Brand<string, "ArtifactId">;
export type ChangeSetId    = Brand<string, "ChangeSetId">;
export type Scenario       = Brand<string, "Scenario">;
export type EnvironmentTag = Brand<string, "EnvironmentTag">;
export type SuiteId        = Brand<string, "SuiteId">;
export type ContentHash    = Brand<string, "ContentHash">;

// Crockford base32: 0-9 A-H J K M N P-T V-Z (excludes I L O U). ULID is 26 chars.
const ULID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/;
// Scenario / SuiteId: lowercase alnum + hyphen + underscore, non-empty, no path separators.
const SLUG_RE = /^[a-z0-9][a-z0-9_-]*$/;
// EnvironmentTag: slightly more permissive (allows tenant prefixes) but still path-safe.
const ENV_TAG_RE = /^[a-z0-9][a-z0-9_-]*$/i;
// sha256:<64 lowercase hex>.
const CONTENT_HASH_RE = /^sha256:[0-9a-f]{64}$/;

export function newArtifactId(): ArtifactId {
  return ulid() as ArtifactId;
}

export function parseArtifactId(input: string): ArtifactId | null {
  return ULID_RE.test(input) ? (input as ArtifactId) : null;
}

export function newChangeSetId(): ChangeSetId {
  return ulid() as ChangeSetId;
}

export function parseChangeSetId(input: string): ChangeSetId | null {
  return ULID_RE.test(input) ? (input as ChangeSetId) : null;
}

export function parseScenario(input: string): Scenario | null {
  return SLUG_RE.test(input) ? (input as Scenario) : null;
}

export function parseEnvironmentTag(input: string): EnvironmentTag | null {
  if (input === ".." || input.includes("/") || input.includes("\\")) return null;
  return ENV_TAG_RE.test(input) ? (input as EnvironmentTag) : null;
}

export function defaultEnvironmentTag(): EnvironmentTag {
  return "production" as EnvironmentTag;
}

export function parseSuiteId(input: string): SuiteId | null {
  if (input === ".." || input.includes("/") || input.includes("\\")) return null;
  return SLUG_RE.test(input) ? (input as SuiteId) : null;
}

export function parseContentHash(input: string): ContentHash | null {
  return CONTENT_HASH_RE.test(input) ? (input as ContentHash) : null;
}
