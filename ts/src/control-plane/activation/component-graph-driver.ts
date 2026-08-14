import {
  RuntimeComponentGraph,
  validateRuntimeComponentGraph,
  type RuntimeComponentGraphOptions,
  type RuntimeComponentGraphSnapshot,
  type RuntimeComponentManifest,
} from "../../runtimes/component-graph.js";
import {
  RuntimeEffectExecutionMode,
  RuntimeEffectPolicy,
  type RuntimeEffectPolicy as RuntimeEffectPolicyType,
} from "../../runtimes/effect-policy.js";
import type {
  RuntimeActivationDriver,
  RuntimeActivationSession,
  RuntimeActivationSessionInput,
} from "./types.js";

export interface RuntimeComponentGraphActivationDriverOptions {
  readonly resolveManifests: (input: {
    artifactId: string;
    effectPolicy: RuntimeEffectPolicyType;
  }) => Promise<readonly RuntimeComponentManifest[]> | readonly RuntimeComponentManifest[];
  /** Apply candidate configuration to the staged host surface. */
  readonly applyArtifact?: (artifactId: string) => Promise<void> | void;
  readonly validateArtifact?: (artifactId: string) => Promise<void> | void;
  /** Invoke the live revert path and restore baseline host configuration. */
  readonly rollbackArtifact?: (
    candidateArtifactId: string,
    baselineArtifactId: string | null,
  ) => Promise<void> | void;
  /** Restore host configuration during restart or stale-pointer recovery. */
  readonly restoreArtifact?: (
    artifactId: string | null,
    replacedArtifactId: string | null,
  ) => Promise<void> | void;
  readonly drainArtifact?: (artifactId: string) => Promise<void> | void;
  readonly resumeArtifact?: (artifactId: string) => Promise<void> | void;
  readonly graphOptions?: RuntimeComponentGraphOptions;
}

interface LiveGraphSlot {
  readonly artifactId: string;
  readonly mode: RuntimeActivationSessionInput["targetMode"];
  readonly graph: RuntimeComponentGraph;
}

/**
 * Blue/green graph driver used by the trusted activation supervisor.
 * Candidate components activate in a private graph. The live slot changes only
 * after the staged graph is fully active, so failed staging leaves the prior
 * graph untouched.
 */
export class RuntimeComponentGraphActivationDriver implements RuntimeActivationDriver {
  private readonly options: RuntimeComponentGraphActivationDriverOptions;
  private active: LiveGraphSlot | null = null;
  private readonly deployed = new Map<string, LiveGraphSlot>();
  private readonly cleanupBlockedArtifactIds = new Set<string>();

  constructor(options: RuntimeComponentGraphActivationDriverOptions) {
    this.options = options;
  }

  observedArtifactId(): string | null {
    return this.active?.artifactId ?? null;
  }

  snapshot(): RuntimeComponentGraphSnapshot | null {
    return this.active?.graph.snapshot() ?? null;
  }

  isActivated(
    artifactId: string,
    mode: RuntimeActivationSessionInput["targetMode"],
  ): boolean {
    if (mode === "active") return this.active?.artifactId === artifactId;
    return this.deployed.get(artifactId)?.mode === mode;
  }

  acknowledgeCleanupRepair(artifactId: string): void {
    this.cleanupBlockedArtifactIds.delete(artifactId);
  }

  async beginActivation(input: RuntimeActivationSessionInput): Promise<RuntimeActivationSession> {
    const prior = this.active;
    if ((prior?.artifactId ?? null) !== input.priorArtifactId) {
      throw new Error("runtime observed state does not match the activation baseline");
    }
    if (
      prior?.artifactId === input.candidateArtifactId
      && input.targetMode !== "active"
    ) {
      throw new Error("the active artifact cannot also be deployed as a sidecar mode");
    }

    let manifests: readonly RuntimeComponentManifest[] | undefined;
    let staged: LiveGraphSlot | undefined;
    let drained = false;
    let cutover = false;
    let priorDisposalFailed = false;
    const loadManifests = async (): Promise<readonly RuntimeComponentManifest[]> => {
      manifests ??= await this.options.resolveManifests({
        artifactId: input.candidateArtifactId,
        effectPolicy: input.effectPolicy,
      });
      return manifests;
    };

    return {
      apply: async () => {
        await this.options.applyArtifact?.(input.candidateArtifactId);
      },
      validate: async () => {
        await this.options.validateArtifact?.(input.candidateArtifactId);
        validateRuntimeComponentGraph(await loadManifests());
      },
      activate: async () => {
        const graph = new RuntimeComponentGraph(this.options.graphOptions);
        const snapshot = await graph.reconcile(await loadManifests());
        staged = { artifactId: input.candidateArtifactId, mode: input.targetMode, graph };
        assertFullyActive(snapshot);
      },
      drainPrior: async () => {
        if (!prior || input.targetMode !== "active") return;
        await this.options.drainArtifact?.(prior.artifactId);
        drained = true;
      },
      cutover: async () => {
        if (!staged) throw new Error("candidate graph has not activated");
        const existing = this.deployed.get(staged.artifactId);
        if (existing && existing !== staged) {
          assertCleanupComplete(await existing.graph.reconcile([]));
          this.deployed.delete(existing.artifactId);
        }
        this.deployed.set(staged.artifactId, staged);
        if (input.targetMode === "active") this.active = staged;
        cutover = true;
      },
      disposePrior: async () => {
        if (!prior || input.targetMode !== "active") return;
        const snapshot = await prior.graph.reconcile([]);
        if (snapshot.blockedCapabilities.length > 0) {
          priorDisposalFailed = true;
          this.cleanupBlockedArtifactIds.add(prior.artifactId);
          throw new Error("prior provider cleanup failed");
        }
        this.deployed.delete(prior.artifactId);
      },
      abort: async () => {
        if (priorDisposalFailed) {
          throw new Error("prior runtime requires explicit cleanup repair");
        }
        if (staged) {
          assertCleanupComplete(await staged.graph.reconcile([]));
          this.deployed.delete(staged.artifactId);
        }
        await this.options.rollbackArtifact?.(
          input.candidateArtifactId,
          input.priorArtifactId,
        );
        if (prior) {
          this.active = prior;
          if (drained) await this.options.resumeArtifact?.(prior.artifactId);
        } else {
          this.active = null;
        }
        if (
          cutover
          && input.targetMode === "active"
          && this.active?.artifactId !== input.priorArtifactId
        ) {
          throw new Error("candidate abort could not restore prior live slot");
        }
      },
    };
  }

  async rollback(input: {
    transactionId: string;
    candidateArtifactId: string;
    baselineArtifactId: string | null;
  }): Promise<void> {
    const candidate = this.deployed.get(input.candidateArtifactId);
    if (!candidate) {
      if (this.observedArtifactId() === input.baselineArtifactId) return;
      throw new Error("runtime rollback candidate is not the observed active artifact");
    }
    await this.options.rollbackArtifact?.(
      input.candidateArtifactId,
      input.baselineArtifactId,
    );
    if (
      input.baselineArtifactId !== null
      && this.cleanupBlockedArtifactIds.has(input.baselineArtifactId)
    ) {
      throw new Error("baseline runtime cleanup requires explicit supervisor repair");
    }
    await this.options.drainArtifact?.(input.candidateArtifactId);
    if (candidate.mode === "active") {
      await this.restoreRuntimeOnly(input.baselineArtifactId);
    } else {
      assertCleanupComplete(await candidate.graph.reconcile([]));
      this.deployed.delete(candidate.artifactId);
    }
  }

  async restore(artifactId: string | null): Promise<void> {
    if (this.observedArtifactId() === artifactId) return;
    const replacedArtifactId = this.observedArtifactId();
    await this.options.restoreArtifact?.(artifactId, replacedArtifactId);
    await this.restoreRuntimeOnly(artifactId);
  }

  private async restoreRuntimeOnly(artifactId: string | null): Promise<void> {
    if (artifactId !== null && this.cleanupBlockedArtifactIds.has(artifactId)) {
      throw new Error("runtime artifact cleanup requires explicit supervisor repair");
    }
    const replaced = this.active;
    if (artifactId === null) {
      this.active = null;
      if (replaced) {
        assertCleanupComplete(await replaced.graph.reconcile([]));
        this.deployed.delete(replaced.artifactId);
      }
      return;
    }

    const policy = new RuntimeEffectPolicy({ mode: RuntimeEffectExecutionMode.ACTIVE });
    const manifests = await this.options.resolveManifests({ artifactId, effectPolicy: policy });
    validateRuntimeComponentGraph(manifests);
    const graph = new RuntimeComponentGraph(this.options.graphOptions);
    assertFullyActive(await graph.reconcile(manifests));
    const restored = { artifactId, mode: "active" as const, graph };
    this.active = restored;
    this.deployed.set(artifactId, restored);
    if (replaced) {
      assertCleanupComplete(await replaced.graph.reconcile([]));
      this.deployed.delete(replaced.artifactId);
    }
  }
}

function assertFullyActive(snapshot: RuntimeComponentGraphSnapshot): void {
  if (
    snapshot.blockedCapabilities.length > 0
    || snapshot.blockedComponentIds.length > 0
    || snapshot.components.some((component) => component.state !== "active")
  ) {
    throw new Error("runtime component graph did not fully activate");
  }
}

function assertCleanupComplete(snapshot: RuntimeComponentGraphSnapshot): void {
  if (
    snapshot.blockedCapabilities.length > 0
    || snapshot.blockedComponentIds.length > 0
  ) {
    throw new Error("runtime component graph cleanup requires supervisor repair");
  }
}
