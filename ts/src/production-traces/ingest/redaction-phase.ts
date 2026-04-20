import type { ProductionTrace } from "../contract/types.js";
import { markRedactions as markRedactionsImpl } from "../redaction/mark.js";
import { applyRedactions as applyRedactionsImpl } from "../redaction/apply.js";
import { loadRedactionPolicy as loadRedactionPolicyImpl } from "../redaction/policy.js";
import { loadInstallSalt as loadInstallSaltImpl } from "../redaction/install-salt.js";
import type { LoadedRedactionPolicy } from "../redaction/types.js";

/**
 * The single seam between `ingest/` and `redaction/` (spec §3.2).
 *
 * All ingest-layer code that needs redaction-subsystem primitives routes
 * through this module — policy loading, salt loading, mark, apply. Keeping
 * the import discipline narrow here lets Layer 5+ refactor the redaction
 * internals without touching scan-workflow.
 */

export type { LoadedRedactionPolicy } from "../redaction/types.js";

/**
 * Mark-at-ingest redaction detection — spec §7.2.
 *
 * The scan workflow calls this exactly once per trace, passing a pre-loaded
 * policy (loaded once at workflow init by `loadRedactionPolicy`).
 *
 * Layer 4 semantics:
 *   - Client-provided markers preserved unchanged.
 *   - Auto-detection runs policy's configured categories over message
 *     content, tool call args/result, outcome.reasoning, feedbackRefs[].comment.
 *   - Custom patterns from policy applied.
 *   - If metadata.rawProviderPayload is present, blanket marker is added.
 *   - Duplicates collapsed by (path, category).
 */
export function markRedactions(
  trace: ProductionTrace,
  policy: LoadedRedactionPolicy,
): ProductionTrace {
  return markRedactionsImpl(trace, policy);
}

/**
 * Apply-at-export redaction — spec §7.3 / §7.6.
 *
 * The scan workflow calls this only when `policy.mode === "on-ingest"`, so
 * nothing plaintext-sensitive is ever written to `ingested/`.
 */
export function applyRedactions(
  trace: ProductionTrace,
  policy: LoadedRedactionPolicy,
  installSalt: string | null,
): ProductionTrace {
  return applyRedactionsImpl(trace, policy, installSalt);
}

/** Load the per-installation redaction policy (defaults if missing). */
export async function loadRedactionPolicy(cwd: string): Promise<LoadedRedactionPolicy> {
  return loadRedactionPolicyImpl(cwd);
}

/** Load the per-installation salt used for hash-action category overrides. */
export async function loadInstallSalt(cwd: string): Promise<string | null> {
  return loadInstallSaltImpl(cwd);
}
