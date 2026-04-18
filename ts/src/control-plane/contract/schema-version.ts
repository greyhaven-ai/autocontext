declare const brand: unique symbol;
type Brand<T, B> = T & { readonly [brand]: B };

export type SchemaVersion = Brand<string, "SchemaVersion">;

export const CURRENT_SCHEMA_VERSION: SchemaVersion = "1.0" as SchemaVersion;

// MAJOR.MINOR without leading zeros on either component.
const VERSION_RE = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;

export function parseSchemaVersion(input: string): SchemaVersion | null {
  return VERSION_RE.test(input) ? (input as SchemaVersion) : null;
}

function split(v: SchemaVersion): { major: number; minor: number } {
  const match = v.match(VERSION_RE)!;
  return { major: Number(match[1]), minor: Number(match[2]) };
}

export function compareSchemaVersions(a: SchemaVersion, b: SchemaVersion): number {
  const av = split(a);
  const bv = split(b);
  if (av.major !== bv.major) return av.major - bv.major;
  return av.minor - bv.minor;
}

export function isReadCompatible(
  docVersion: SchemaVersion,
  consumerVersion: SchemaVersion,
): boolean {
  return split(docVersion).major === split(consumerVersion).major;
}

export function canWriteVersion(
  targetVersion: SchemaVersion,
  declaredRepoVersion: SchemaVersion,
): boolean {
  return compareSchemaVersions(targetVersion, declaredRepoVersion) >= 0;
}
