// Actuator registry — a pure in-process map from ActuatorType to its registration.
//
// Registration enforces two static rules at registration time (fail fast):
//   1. `allowedTargetPattern` is a non-empty string.
//   2. The declared `rollback.kind` matches the minimum required strategy for
//      the actuator type:
//        prompt-patch     → content-revert
//        tool-policy      → content-revert
//        routing-rule     → cascade-set
//        fine-tuned-model → pointer-flip
//
// A second call for an already-registered type throws — by design, there is one
// canonical implementation per ActuatorType in a given process.

import type { ArtifactId, EnvironmentTag, Scenario } from "../contract/branded-ids.js";
import type {
  ActuatorType,
  Artifact,
  Patch,
  RollbackStrategy,
} from "../contract/types.js";

/**
 * Narrow view of a WorkspaceLayout suitable for actuators. The `emit/` layer
 * owns the concrete WorkspaceLayout type; actuators only need this subset and
 * must accept the branded Scenario/EnvironmentTag types so callers can pass
 * `artifact.scenario` / `artifact.environmentTag` without a cast.
 */
export interface WorkspaceLayoutArg {
  readonly scenarioDir: (scenario: Scenario, env: EnvironmentTag) => string;
  readonly promptSubdir: string;
  readonly policySubdir: string;
  readonly routingSubdir: string;
  readonly modelPointerSubdir: string;
}

/**
 * The contract every concrete actuator implements. `P` is the parsed
 * payload shape (produced by `parsePayload`).
 */
export interface Actuator<P> {
  /**
   * Parse/validate a raw payload object (typically the decoded JSON for a
   * `<payload>/<single-file>.json` or the content of a text payload file).
   * Throws a ZodError / Error on failure.
   */
  parsePayload(raw: unknown): P;

  /**
   * Resolve the working-tree target path for this artifact given a layout.
   * Must be deterministic given the inputs. Does not touch disk.
   */
  resolveTargetPath(artifact: Artifact, layout: WorkspaceLayoutArg): string;

  /**
   * Apply the artifact's payload to the working tree — typically writing
   * the payload file to `resolveTargetPath(artifact, layout)`. Verifies the
   * on-disk payload tree hash matches `artifact.payloadHash` before writing.
   */
  apply(args: {
    artifact: Artifact;
    payloadDir: string;
    workingTreeRoot: string;
    layout: WorkspaceLayoutArg;
  }): Promise<void>;

  /**
   * Produce the Patch that describes what `apply` would do — used by the emit
   * pipeline to build PR bodies. Pure; does not touch disk (other than reading
   * the current working-tree file if one exists).
   */
  emitPatch(args: {
    artifact: Artifact;
    payloadDir: string;
    workingTreeRoot: string;
    layout: WorkspaceLayoutArg;
  }): Patch;

  /**
   * Produce the rollback Patch(es) to revert the given candidate back to
   * `baseline`. The strategy is determined by the actuator's registration.
   */
  rollback(args: {
    candidate: Artifact;
    baseline: Artifact;
    candidatePayloadDir: string;
    baselinePayloadDir: string;
    workingTreeRoot: string;
    layout: WorkspaceLayoutArg;
    dependentsInIncompatibleState?: readonly ArtifactId[];
  }): Promise<Patch | Patch[]>;
}

export interface ActuatorRegistration<P> {
  readonly type: ActuatorType;
  readonly rollback: RollbackStrategy;
  /** Glob-style pattern a resolved target path must match. Declarative in v1. */
  readonly allowedTargetPattern: string;
  readonly actuator: Actuator<P>;
}

// Minimum rollback strategy each actuator type must declare.
const MIN_ROLLBACK: Record<ActuatorType, RollbackStrategy["kind"]> = {
  "prompt-patch": "content-revert",
  "tool-policy": "content-revert",
  "routing-rule": "cascade-set",
  "fine-tuned-model": "pointer-flip",
};

function meetsMinimum(type: ActuatorType, declared: RollbackStrategy["kind"]): boolean {
  return declared === MIN_ROLLBACK[type];
}

// ---------- module state ----------

const REGISTRY = new Map<ActuatorType, ActuatorRegistration<unknown>>();

export function registerActuator<P>(reg: ActuatorRegistration<P>): void {
  if (typeof reg.allowedTargetPattern !== "string" || reg.allowedTargetPattern.length === 0) {
    throw new Error(
      `registerActuator(${reg.type}): allowedTargetPattern must be a non-empty string`,
    );
  }
  if (REGISTRY.has(reg.type)) {
    throw new Error(`registerActuator(${reg.type}): actuator already registered`);
  }
  if (!meetsMinimum(reg.type, reg.rollback.kind)) {
    throw new Error(
      `registerActuator(${reg.type}): rollback strategy '${reg.rollback.kind}' `
      + `does not meet minimum '${MIN_ROLLBACK[reg.type]}' for this actuator type`,
    );
  }
  REGISTRY.set(reg.type, reg as ActuatorRegistration<unknown>);
}

export function getActuator(type: ActuatorType): ActuatorRegistration<unknown> | null {
  return REGISTRY.get(type) ?? null;
}

export function listActuatorTypes(): ActuatorType[] {
  return Array.from(REGISTRY.keys());
}

/**
 * Test hook — do NOT call from production code. Clears the registry so each test
 * starts from a known empty state. Concrete actuator modules register on import;
 * call this only in unit tests that verify the registry itself or the actuators.
 */
export function __resetActuatorRegistryForTests(): void {
  REGISTRY.clear();
}
