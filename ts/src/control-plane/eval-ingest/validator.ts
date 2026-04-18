// Business-rule validator for EvalRuns prior to ingestion.
//
// Combines the contract's schema validator with three extra checks that the
// JSON schema alone cannot enforce:
//   - artifactId must resolve to a known artifact in the registry
//   - suiteId + runId must be non-empty
//   - all numeric metric fields must be finite (JSON Schema permits NaN/Infinity
//     under some encoders; we reject unconditionally per spec §6.1)
//
// Import discipline (§3.2): imports contract/ and registry/ only.

import { parseContentHash } from "../contract/branded-ids.js";
import type { EvalRun, MetricBundle, ValidationResult } from "../contract/types.js";
import { validateEvalRun } from "../contract/validators.js";
import type { Registry } from "../registry/index.js";

export interface ValidateEvalRunForIngestionContext {
  readonly registry: Pick<Registry, "loadArtifact">;
}

/**
 * Run the schema validator first, then business rules. Returns a ValidationResult
 * whose `errors` aggregate every failure (not short-circuit) so callers can show
 * all issues at once.
 */
export function validateEvalRunForIngestion(
  input: unknown,
  ctx: ValidateEvalRunForIngestionContext,
): ValidationResult {
  const errors: string[] = [];

  // Schema-level validation first. If it fails we still proceed for the
  // business rules that are checkable from raw input, but we only look at
  // fields we can read safely.
  const schema = validateEvalRun(input);
  if (!schema.valid) {
    errors.push(...schema.errors);
  }

  const maybeRun = input as Partial<EvalRun> | null;
  if (maybeRun === null || typeof maybeRun !== "object") {
    errors.push("<root> input is not an object");
    return { valid: false, errors };
  }

  // suiteId non-empty
  if (typeof maybeRun.suiteId !== "string" || maybeRun.suiteId.length === 0) {
    errors.push("/suiteId must be a non-empty string");
  }

  // runId non-empty
  if (typeof maybeRun.runId !== "string" || maybeRun.runId.length === 0) {
    errors.push("/runId must be a non-empty string");
  }

  // datasetProvenance.sliceHash is a valid ContentHash
  const sliceHash = maybeRun.datasetProvenance?.sliceHash;
  if (typeof sliceHash !== "string" || parseContentHash(sliceHash) === null) {
    errors.push(`/datasetProvenance/sliceHash is not a valid ContentHash`);
  }

  // evalRunnerIdentity.configHash is a valid ContentHash
  const configHash = maybeRun.metrics?.evalRunnerIdentity?.configHash;
  if (typeof configHash !== "string" || parseContentHash(configHash) === null) {
    errors.push(`/metrics/evalRunnerIdentity/configHash is not a valid ContentHash`);
  }

  // Numeric fields in MetricBundle must be finite.
  if (maybeRun.metrics !== undefined && maybeRun.metrics !== null) {
    collectNonFiniteFields(maybeRun.metrics, errors);
  }

  // artifactId must exist in registry (last — loadArtifact may throw).
  if (typeof maybeRun.artifactId === "string" && maybeRun.artifactId.length > 0) {
    try {
      ctx.registry.loadArtifact(maybeRun.artifactId as EvalRun["artifactId"]);
    } catch (err) {
      errors.push(
        `/artifactId unknown artifact ${maybeRun.artifactId}: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  if (errors.length === 0) {
    return { valid: true };
  }
  return { valid: false, errors };
}

function collectNonFiniteFields(metrics: Partial<MetricBundle>, errors: string[]): void {
  const checks: Array<[string, unknown]> = [
    ["/metrics/quality/score", metrics.quality?.score],
    ["/metrics/quality/sampleSize", metrics.quality?.sampleSize],
    ["/metrics/cost/tokensIn", metrics.cost?.tokensIn],
    ["/metrics/cost/tokensOut", metrics.cost?.tokensOut],
    ["/metrics/cost/usd", metrics.cost?.usd],
    ["/metrics/latency/p50Ms", metrics.latency?.p50Ms],
    ["/metrics/latency/p95Ms", metrics.latency?.p95Ms],
    ["/metrics/latency/p99Ms", metrics.latency?.p99Ms],
  ];
  for (const [path, value] of checks) {
    if (value === undefined) continue;
    if (typeof value !== "number" || !Number.isFinite(value)) {
      errors.push(`${path} must be finite (got ${String(value)})`);
    }
  }
  const hf = metrics.humanFeedback;
  if (hf !== undefined) {
    for (const k of ["positive", "negative", "neutral"] as const) {
      const v = hf[k];
      if (typeof v !== "number" || !Number.isFinite(v)) {
        errors.push(`/metrics/humanFeedback/${k} must be finite (got ${String(v)})`);
      }
    }
  }
}
