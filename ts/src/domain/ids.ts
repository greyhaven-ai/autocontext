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
 *   - HTTP route parsing, DB reads, and CLI arg parsing are boundaries:
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
 *     transformation, it exists purely to document the intent at the call
 *     site instead of reaching for an `as string` cast.
 *
 * Validation is deliberately byte-empty-only (`value.length === 0`), not
 * whitespace-trimmed. Several downstream consumers (e.g. `cockpit-api.ts`'s
 * `contextSelection`) trim internally and turn a whitespace-only id into a
 * specific 404/422, and callers rely on that HTTP-layer behavior. A
 * trim-based check here would throw before that code ever runs, replacing
 * a handled 4xx with an unhandled 500. These constructors exist to add a
 * type boundary, not to change what counts as a valid id.
 */

declare const brand: unique symbol;
type Brand<T, B> = T & { readonly [brand]: B };

export type RunId = Brand<string, "RunId">;
export type ScenarioName = Brand<string, "ScenarioName">;
export type DbPath = Brand<string, "DbPath">;

/**
 * Construct a `RunId` from a raw string. Throws only if the value is
 * byte-empty (see the module doc comment for why this doesn't also reject
 * whitespace-only input).
 */
export function asRunId(value: string): RunId {
  if (value.length === 0) {
    throw new Error("RunId must be a non-empty string");
  }
  return value as RunId;
}

/**
 * Construct a `ScenarioName` from a raw string. Throws only if the value is
 * byte-empty (see the module doc comment for why this doesn't also reject
 * whitespace-only input).
 */
export function asScenarioName(value: string): ScenarioName {
  if (value.length === 0) {
    throw new Error("ScenarioName must be a non-empty string");
  }
  return value as ScenarioName;
}

/**
 * Construct a `DbPath` from a raw string. Throws only if the value is
 * byte-empty (see the module doc comment for why this doesn't also reject
 * whitespace-only input).
 */
export function asDbPath(value: string): DbPath {
  if (value.length === 0) {
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
