import type {
  RuntimeComponentDisposer,
  RuntimeComponentScope,
} from "./component-lifecycle.js";

export const RuntimeEffectClass = {
  REVERSIBLE: "reversible",
  COMPENSATABLE: "compensatable",
  IRREVERSIBLE: "irreversible",
} as const;

export type RuntimeEffectClass =
  (typeof RuntimeEffectClass)[keyof typeof RuntimeEffectClass];

export const RuntimeEffectExecutionMode = {
  ACTIVE: "active",
  CANDIDATE: "candidate",
  SHADOW: "shadow",
  CANARY: "canary",
} as const;

export type RuntimeEffectExecutionMode =
  (typeof RuntimeEffectExecutionMode)[keyof typeof RuntimeEffectExecutionMode];

export type RuntimeEffectSandboxBoundary =
  | "in_process"
  | "process"
  | "interpreter"
  | "microvm";

export interface ReversibleRuntimeEffect {
  readonly effectClass: "reversible";
  readonly disposer: RuntimeComponentDisposer;
}

export interface RuntimeEffectCompensation {
  readonly compensate: RuntimeComponentDisposer;
  readonly idempotencyKey: string;
  readonly observationalEquivalence: string;
  readonly journaledBeforeInvoke: true;
}

export interface CompensatableRuntimeEffect {
  readonly effectClass: "compensatable";
  readonly compensation: RuntimeEffectCompensation;
}

export interface IrreversibleRuntimeEffect {
  readonly effectClass: "irreversible";
  readonly commitBoundary: string;
}

export type RuntimeEffectDeclaration =
  | ReversibleRuntimeEffect
  | CompensatableRuntimeEffect
  | IrreversibleRuntimeEffect;

export interface RuntimeEffectSandboxPolicy {
  readonly boundary: RuntimeEffectSandboxBoundary;
  readonly available: boolean;
}

export interface RuntimeEffectPolicyOptions {
  readonly mode: RuntimeEffectExecutionMode;
  readonly untrustedComponent?: boolean;
  readonly sandbox?: RuntimeEffectSandboxPolicy;
  readonly allowIrreversible?: boolean;
  readonly committed?: boolean;
  readonly commitBoundaryId?: string;
}

export type RuntimeEffectPolicyErrorCode =
  | "effect_metadata_required"
  | "reversible_disposer_required"
  | "compensation_metadata_required"
  | "component_scope_required"
  | "irreversible_effect_requires_commit"
  | "external_sandbox_required"
  | "external_sandbox_unavailable";

export class RuntimeEffectPolicyError extends Error {
  readonly code: RuntimeEffectPolicyErrorCode;

  constructor(code: RuntimeEffectPolicyErrorCode) {
    super(runtimeEffectPolicyErrorMessage(code));
    this.name = "RuntimeEffectPolicyError";
    this.code = code;
  }
}

/**
 * Host-owned policy for candidate runtime effects.
 *
 * The workspace keeps this object in a private field and exposes no candidate
 * mutation API. Untrusted components still require an external isolation
 * boundary; this policy is an invocation gate, not a sandbox.
 */
export class RuntimeEffectPolicy {
  readonly mode: RuntimeEffectExecutionMode;
  readonly untrustedComponent: boolean;
  readonly sandbox?: RuntimeEffectSandboxPolicy;
  readonly allowIrreversible: boolean;
  readonly committed: boolean;
  readonly commitBoundaryId: string;

  constructor(options: RuntimeEffectPolicyOptions) {
    this.mode = options.mode;
    this.untrustedComponent = options.untrustedComponent ?? false;
    this.sandbox = options.sandbox ? { ...options.sandbox } : undefined;
    this.allowIrreversible = options.allowIrreversible ?? false;
    this.committed = options.committed ?? false;
    this.commitBoundaryId = options.commitBoundaryId?.trim() ?? "";
    this.assertSandboxBoundary();
    Object.freeze(this.sandbox);
    Object.freeze(this);
  }

  authorize(
    declaration: RuntimeEffectDeclaration | undefined,
    componentScope?: RuntimeComponentScope,
  ): RuntimeEffectClass {
    if (!declaration) {
      throw new RuntimeEffectPolicyError("effect_metadata_required");
    }
    assertRuntimeEffectDeclaration(declaration);

    if (declaration.effectClass === RuntimeEffectClass.IRREVERSIBLE) {
      if (
        this.mode !== RuntimeEffectExecutionMode.ACTIVE
        && (
          !this.allowIrreversible
          || !this.committed
          || !this.commitBoundaryId
          || declaration.commitBoundary !== this.commitBoundaryId
        )
      ) {
        throw new RuntimeEffectPolicyError("irreversible_effect_requires_commit");
      }
      return declaration.effectClass;
    }

    if (!componentScope) {
      throw new RuntimeEffectPolicyError("component_scope_required");
    }
    if (declaration.effectClass === RuntimeEffectClass.REVERSIBLE) {
      componentScope.defer(declaration.disposer);
    } else {
      componentScope.defer(declaration.compensation.compensate);
    }
    return declaration.effectClass;
  }

  private assertSandboxBoundary(): void {
    if (!this.untrustedComponent) return;
    if (
      !this.sandbox
      || !new Set<RuntimeEffectSandboxBoundary>([
        "process",
        "interpreter",
        "microvm",
      ]).has(this.sandbox.boundary)
    ) {
      throw new RuntimeEffectPolicyError("external_sandbox_required");
    }
    if (!this.sandbox.available) {
      throw new RuntimeEffectPolicyError("external_sandbox_unavailable");
    }
  }
}

export function assertRuntimeEffectDeclaration(
  declaration: RuntimeEffectDeclaration,
): void {
  if (!isRecord(declaration)) {
    throw new RuntimeEffectPolicyError("effect_metadata_required");
  }
  if (declaration.effectClass === RuntimeEffectClass.REVERSIBLE) {
    if (typeof declaration.disposer !== "function") {
      throw new RuntimeEffectPolicyError("reversible_disposer_required");
    }
    return;
  }
  if (declaration.effectClass === RuntimeEffectClass.COMPENSATABLE) {
    const compensation = declaration.compensation;
    if (
      !isRecord(compensation)
      || typeof compensation.compensate !== "function"
      || !isNonEmptyString(compensation.idempotencyKey)
      || !isNonEmptyString(compensation.observationalEquivalence)
      || compensation.journaledBeforeInvoke !== true
    ) {
      throw new RuntimeEffectPolicyError("compensation_metadata_required");
    }
    return;
  }
  if (
    declaration.effectClass === RuntimeEffectClass.IRREVERSIBLE
    && isNonEmptyString(declaration.commitBoundary)
  ) {
    return;
  }
  throw new RuntimeEffectPolicyError("effect_metadata_required");
}

export function runtimeEffectClassForAudit(
  declaration: RuntimeEffectDeclaration | undefined,
): RuntimeEffectClass | "undeclared" {
  if (!declaration || !isRecord(declaration)) return "undeclared";
  const effectClass = declaration.effectClass;
  return effectClass === RuntimeEffectClass.REVERSIBLE
    || effectClass === RuntimeEffectClass.COMPENSATABLE
    || effectClass === RuntimeEffectClass.IRREVERSIBLE
    ? effectClass
    : "undeclared";
}

export function runtimeEffectPolicyErrorCode(error: unknown): RuntimeEffectPolicyErrorCode {
  return error instanceof RuntimeEffectPolicyError ? error.code : "effect_metadata_required";
}

function runtimeEffectPolicyErrorMessage(code: RuntimeEffectPolicyErrorCode): string {
  switch (code) {
    case "effect_metadata_required":
      return "runtime effect metadata is required by policy";
    case "reversible_disposer_required":
      return "reversible runtime effects require a disposer";
    case "compensation_metadata_required":
      return "compensatable runtime effects require durable compensation metadata";
    case "component_scope_required":
      return "reversible and compensatable effects require a component scope";
    case "irreversible_effect_requires_commit":
      return "irreversible runtime effect is not authorized before its commit boundary";
    case "external_sandbox_required":
      return "untrusted runtime components require an external sandbox boundary";
    case "external_sandbox_unavailable":
      return "the required external sandbox boundary is unavailable";
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}
