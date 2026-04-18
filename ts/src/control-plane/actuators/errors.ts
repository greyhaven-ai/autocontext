// Errors thrown by actuators. Exported from actuators/index.ts for callers.

import type { ArtifactId } from "../contract/branded-ids.js";

/**
 * Thrown by a cascade-set rollback (currently only routing-rule) when the
 * caller attempts to roll back a candidate whose active dependents have not
 * yet been reverted to compatible state. The caller (typically the emit
 * pipeline) must first roll back the dependents and then retry.
 *
 * Carries the list of offending dependents so the caller can orchestrate
 * cascading rollback deterministically.
 */
export class CascadeRollbackRequired extends Error {
  public readonly name = "CascadeRollbackRequired" as const;
  public readonly dependents: readonly ArtifactId[];

  constructor(message: string, dependents: readonly ArtifactId[]) {
    super(message);
    // Defensive copy — callers sometimes mutate the source array.
    this.dependents = Object.freeze([...dependents]);
    // Restore prototype chain for correct instanceof in ES5-transpiled callers.
    Object.setPrototypeOf(this, CascadeRollbackRequired.prototype);
  }
}
