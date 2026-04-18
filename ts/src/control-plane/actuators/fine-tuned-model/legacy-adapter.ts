// Legacy model-record adapter — STUB.
//
// Migrates pre-control-plane model records (stored under the prior
// training/registry shape) into first-class fine-tuned-model Artifacts.
// The real implementation lands in Layer 11 (cutover & migration). This
// file exists now so the signature is stable and callers in later layers
// can compile against it.

/**
 * Opaque placeholder for the Registry type — we cannot import the real
 * `Registry` interface from `registry/` here (§3.2 import discipline:
 * actuators/ must not depend on registry/). The real Layer 11 code lives
 * in a higher layer that can see both, so it'll widen this signature then.
 */
type RegistryLike = unknown;

/**
 * Import legacy pre-control-plane model records as fine-tuned-model Artifacts.
 * Returns counts for progress reporting.
 *
 * Currently unimplemented — calling this throws. See Layer 11.
 */
export async function importLegacyModelRecords(
  cwd: string,
  registry: RegistryLike,
): Promise<{ imported: number; skipped: number }> {
  // Silence unused warnings in the stub — the Layer 11 impl will consume both.
  void cwd;
  void registry;
  throw new Error("not-implemented: legacy-adapter migration lands in Layer 11");
}
