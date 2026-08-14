import type { Artifact } from "../contract/types.js";
import { parseArtifactId } from "../contract/branded-ids.js";
import { createPromotionEvent } from "../contract/factories.js";
import type { Registry } from "../registry/index.js";
import type {
  RuntimeActivationRequest,
  RuntimeActivationResult,
  RuntimeActivationTargetMode,
} from "./types.js";
import { RuntimeActivationSupervisor } from "./supervisor.js";

export interface RegistryRuntimeActivationControllerOptions {
  readonly registry: Registry;
  readonly supervisor: RuntimeActivationSupervisor;
  readonly now?: () => string;
}

export interface RegistryRuntimePromotionRequest extends RuntimeActivationRequest {
  readonly reason: string;
}

export interface RegistryRuntimeRollbackRequest {
  readonly transactionId: string;
  readonly candidateArtifactId: string;
  readonly baselineArtifactId: string | null;
  readonly reason: string;
}

export interface RegistryRuntimeActivationResult {
  readonly runtime: RuntimeActivationResult;
  readonly candidate: Artifact;
  readonly baseline: Artifact | null;
}

/**
 * Joins the durable live-runtime transaction to the existing promotion state
 * machine. Metadata is advanced only after the live operation succeeds. If the
 * metadata update fails, a second durable transaction compensates the live
 * cutover and restores the prior registry state before the error is rethrown.
 */
export class RegistryRuntimeActivationController {
  private readonly registry: Registry;
  private readonly supervisor: RuntimeActivationSupervisor;
  private readonly now: () => string;

  constructor(options: RegistryRuntimeActivationControllerOptions) {
    this.registry = options.registry;
    this.supervisor = options.supervisor;
    this.now = options.now ?? (() => new Date().toISOString());
  }

  async promote(
    request: RegistryRuntimePromotionRequest,
  ): Promise<RegistryRuntimeActivationResult> {
    assertPromotionTarget(request.targetMode);
    const candidateId = requiredArtifactId(request.candidateArtifactId);
    const before = this.registry.loadArtifact(candidateId);
    const baseline = this.registry.getActive(
      before.scenario,
      before.actuatorType,
      before.environmentTag,
    );
    const runtime = await this.supervisor.activate(request);
    if (runtime.outcome !== "succeeded") {
      return { runtime, candidate: this.registry.loadArtifact(candidateId), baseline };
    }

    try {
      const current = this.registry.loadArtifact(candidateId);
      const candidate = current.activationState === request.targetMode
        ? current
        : this.registry.appendPromotionEvent(candidateId, createPromotionEvent({
            from: current.activationState,
            to: request.targetMode,
            reason: request.reason,
            timestamp: this.now(),
          }));
      return { runtime, candidate, baseline };
    } catch (metadataError) {
      const compensated = await this.compensatePromotion(request, baseline);
      await this.supervisor.markCompensated(
        request.transactionId,
        compensated.outcome !== "succeeded",
      );
      throw metadataError;
    }
  }

  async rollback(
    request: RegistryRuntimeRollbackRequest,
  ): Promise<RegistryRuntimeActivationResult> {
    const candidateId = requiredArtifactId(request.candidateArtifactId);
    const baselineId = request.baselineArtifactId === null
      ? null
      : requiredArtifactId(request.baselineArtifactId);
    const runtime = await this.supervisor.rollback({
      transactionId: request.transactionId,
      candidateArtifactId: request.candidateArtifactId,
      baselineArtifactId: request.baselineArtifactId,
    });
    let candidate = this.registry.loadArtifact(candidateId);
    let baseline = baselineId ? this.registry.loadArtifact(baselineId) : null;
    if (runtime.outcome !== "succeeded") return { runtime, candidate, baseline };

    if (candidate.activationState !== "candidate") {
      candidate = this.registry.appendPromotionEvent(candidate.id, createPromotionEvent({
        from: candidate.activationState,
        to: "candidate",
        reason: request.reason,
        timestamp: this.now(),
      }));
    }
    if (baseline) baseline = this.restoreBaselineMetadata(baseline, request.reason);
    return { runtime, candidate, baseline };
  }

  private async compensatePromotion(
    request: RegistryRuntimePromotionRequest,
    baseline: Artifact | null,
  ): Promise<RuntimeActivationResult> {
    const runtime = await this.supervisor.rollback({
      transactionId: `${request.transactionId}-metadata-compensation`,
      candidateArtifactId: request.candidateArtifactId,
      baselineArtifactId: baseline?.id ?? null,
    });
    if (runtime.outcome !== "succeeded") return runtime;
    const candidate = this.registry.loadArtifact(requiredArtifactId(request.candidateArtifactId));
    if (candidate.activationState !== "candidate") {
      this.registry.appendPromotionEvent(candidate.id, createPromotionEvent({
        from: candidate.activationState,
        to: "candidate",
        reason: "runtime activation metadata compensation",
        timestamp: this.now(),
      }));
    }
    if (baseline) this.restoreBaselineMetadata(
      this.registry.loadArtifact(baseline.id),
      "runtime activation metadata compensation",
    );
    return runtime;
  }

  private restoreBaselineMetadata(baseline: Artifact, reason: string): Artifact {
    let current = baseline;
    if (current.activationState === "deprecated" || current.activationState === "disabled") {
      current = this.registry.appendPromotionEvent(current.id, createPromotionEvent({
        from: current.activationState,
        to: "candidate",
        reason,
        timestamp: this.now(),
      }));
    }
    if (current.activationState !== "active") {
      current = this.registry.appendPromotionEvent(current.id, createPromotionEvent({
        from: current.activationState,
        to: "active",
        reason,
        timestamp: this.now(),
      }));
    }
    return current;
  }
}

function assertPromotionTarget(mode: RuntimeActivationTargetMode): asserts mode is Exclude<
  RuntimeActivationTargetMode,
  "candidate"
> {
  if (mode === "candidate") {
    throw new Error("candidate is not a runtime promotion target");
  }
}

function requiredArtifactId(value: string) {
  const parsed = parseArtifactId(value);
  if (parsed === null) throw new Error("runtime activation artifact id is invalid");
  return parsed;
}
