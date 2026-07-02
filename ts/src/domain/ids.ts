/**
 * Branded id types (AC-855).
 *
 * `runId: string` and `scenario: string` are structurally indistinguishable
 * from every other string in the codebase, so nothing stops a scenario name
 * from being passed where a run id is expected (or vice versa). Branding
 * gives each identifier a distinct nominal type at compile time while
 * remaining a plain `string` at runtime (zero behavior change, no
 * serialization concerns).
 *
 * Discipline: convert at the boundary, carry the brand internally.
 *   - HTTP route parsing, DB reads, and CLI arg parsing are boundaries —
 *     call `asRunId` / `asScenarioName` / `asDbPath` once, immediately after
 *     the raw string is obtained.
 *   - Everything downstream that has been migrated to a branded parameter
 *     type carries the brand; no further casts are needed because a branded
 *     type is a structural subtype of `string` (it can be passed anywhere a
 *     `string` is expected, including into modules that have not yet been
 *     migrated).
 *   - `unbrand` is an identity passthrough for the rare case where code
 *     needs the underlying `string` type explicitly (e.g. a generic
 *     `Record<string, unknown>` key). It performs no validation or
 *     transformation — it exists purely to document the intent at the call
 *     site instead of reaching for an `as string` cast.
 */

declare const brand: unique symbol;
type Brand<T, B> = T & { readonly [brand]: B };

export type RunId = Brand<string, "RunId">;
export type ScenarioName = Brand<string, "ScenarioName">;
export type DbPath = Brand<string, "DbPath">;

/**
 * Construct a `RunId` from a raw string. Throws if the value is empty or
 * all-whitespace — a run id is always caller- or ULID-supplied and should
 * never be blank.
 */
export function asRunId(value: string): RunId {
  if (value.trim().length === 0) {
    throw new Error("RunId must be a non-empty string");
  }
  return value as RunId;
}

/**
 * Construct a `ScenarioName` from a raw string. Throws if the value is
 * empty or all-whitespace.
 */
export function asScenarioName(value: string): ScenarioName {
  if (value.trim().length === 0) {
    throw new Error("ScenarioName must be a non-empty string");
  }
  return value as ScenarioName;
}

/**
 * Construct a `DbPath` from a raw string. Throws if the value is empty or
 * all-whitespace.
 */
export function asDbPath(value: string): DbPath {
  if (value.trim().length === 0) {
    throw new Error("DbPath must be a non-empty string");
  }
  return value as DbPath;
}

/**
 * Identity passthrough that documents "I am deliberately dropping the
 * brand here" at the call site, instead of an unexplained `as string`.
 * Performs no runtime transformation.
 */
export function unbrand(value: RunId | ScenarioName | DbPath): string {
  return value;
}
