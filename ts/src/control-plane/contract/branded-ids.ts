import { ulid } from "ulid";

export type {
	ContentHash,
	EnvironmentTag,
	Scenario,
} from "../../production-traces/contract/branded-ids.js";
export {
	defaultEnvironmentTag,
	parseContentHash,
	parseEnvironmentTag,
	parseScenario,
} from "../../production-traces/contract/branded-ids.js";

declare const brand: unique symbol;
type Brand<T, B> = T & { readonly [brand]: B };

export type ArtifactId = Brand<string, "ArtifactId">;
export type ChangeSetId = Brand<string, "ChangeSetId">;
export type HarnessProposalId = Brand<string, "HarnessProposalId">;
export type SuiteId = Brand<string, "SuiteId">;

// Crockford base32: 0-9 A-H J K M N P-T V-Z (excludes I L O U). ULID is 26 chars.
const ULID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/;
// SuiteId: lowercase alnum + hyphen + underscore, non-empty, no path separators.
const SLUG_RE = /^[a-z0-9][a-z0-9_-]*$/;

function toBrand<T extends string>(input: string): Brand<string, T> {
	return input as Brand<string, T>;
}

function parseUlidBrand<T extends string>(input: string): Brand<string, T> | null {
	return ULID_RE.test(input) ? toBrand<T>(input) : null;
}

export function newArtifactId(): ArtifactId {
	return toBrand<"ArtifactId">(ulid());
}

export function parseArtifactId(input: string): ArtifactId | null {
	return parseUlidBrand<"ArtifactId">(input);
}

export function newChangeSetId(): ChangeSetId {
	return toBrand<"ChangeSetId">(ulid());
}

export function parseChangeSetId(input: string): ChangeSetId | null {
	return parseUlidBrand<"ChangeSetId">(input);
}

export function newHarnessProposalId(): HarnessProposalId {
	return toBrand<"HarnessProposalId">(ulid());
}

export function parseHarnessProposalId(input: string): HarnessProposalId | null {
	return parseUlidBrand<"HarnessProposalId">(input);
}

export function parseSuiteId(input: string): SuiteId | null {
	if (input === ".." || input.includes("/") || input.includes("\\"))
		return null;
	return SLUG_RE.test(input) ? toBrand<"SuiteId">(input) : null;
}
