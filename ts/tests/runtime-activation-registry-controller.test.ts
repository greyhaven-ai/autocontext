import { describe, expect, it, vi } from "vitest";

import {
  InMemoryRuntimeActivationJournalStore,
  RegistryRuntimeActivationController,
  RuntimeActivationSupervisor,
  type RuntimeActivationDriver,
  type RuntimeActivationPointer,
  type RuntimeActivationPointerStore,
  type RuntimeActivationTargetMode,
} from "../src/control-plane/activation/index.js";
import {
  createArtifact,
} from "../src/control-plane/contract/factories.js";
import {
  parseArtifactId,
  parseContentHash,
  parseScenario,
  type ArtifactId,
} from "../src/control-plane/contract/branded-ids.js";
import type {
  Artifact,
  PromotionEvent,
} from "../src/control-plane/contract/types.js";
import { appendPromotionEvent } from "../src/control-plane/promotion/append.js";
import type { Registry } from "../src/control-plane/registry/index.js";

describe("RegistryRuntimeActivationController", () => {
  it("advances registry metadata only after live activation and restores both on rollback", async () => {
    const baseline = artifact("01KPEYB3BRQWK2WSHK9E93N6NP");
    const candidate = artifact("01KPEYB3BRYCQ6J235VBR7WBY8");
    const artifacts = new Map<ArtifactId, Artifact>([
      [baseline.id, appendPromotionEvent(baseline, {
        from: "candidate",
        to: "active",
        reason: "initial",
        timestamp: "2026-08-14T00:00:00.000Z",
      })],
      [candidate.id, candidate],
    ]);
    const registry = fakeRegistry(artifacts);
    const runtime = runtimeFixture(baseline.id);
    const controller = new RegistryRuntimeActivationController({
      registry,
      supervisor: runtime.supervisor,
      now: monotonicNow(),
    });

    const promoted = await controller.promote({
      transactionId: "registry-promote",
      candidateArtifactId: candidate.id,
      targetMode: "active",
      reason: "passed evaluation",
    });

    expect(promoted.runtime.outcome).toBe("succeeded");
    expect(promoted.candidate.activationState).toBe("active");
    expect(artifacts.get(baseline.id)?.activationState).toBe("deprecated");
    expect(runtime.observed()).toBe(candidate.id);

    const rolledBack = await controller.rollback({
      transactionId: "registry-rollback",
      candidateArtifactId: candidate.id,
      baselineArtifactId: baseline.id,
      reason: "regression",
    });

    expect(rolledBack.runtime.outcome).toBe("succeeded");
    expect(rolledBack.candidate.activationState).toBe("candidate");
    expect(rolledBack.baseline?.activationState).toBe("active");
    expect(runtime.observed()).toBe(baseline.id);
    expect(runtime.pointer.read()?.artifactId).toBe(baseline.id);
    expect(registry.appendPromotionEvent).toHaveBeenCalled();
  });

  it("durably marks a metadata-compensated cutover so the same key cannot replay it", async () => {
    const baseline = artifact("01KPEYB3BRQWK2WSHK9E93N6NP");
    const candidate = artifact("01KPEYB3BRYCQ6J235VBR7WBY8");
    const artifacts = new Map<ArtifactId, Artifact>([
      [baseline.id, appendPromotionEvent(baseline, {
        from: "candidate",
        to: "active",
        reason: "initial",
        timestamp: "2026-08-14T00:00:00.000Z",
      })],
      [candidate.id, candidate],
    ]);
    const registry = fakeRegistry(artifacts);
    vi.mocked(registry.appendPromotionEvent).mockImplementationOnce(() => {
      throw new Error("metadata store unavailable");
    });
    const runtime = runtimeFixture(baseline.id);
    const controller = new RegistryRuntimeActivationController({
      registry,
      supervisor: runtime.supervisor,
      now: monotonicNow(),
    });
    const request = {
      transactionId: "metadata-failure",
      candidateArtifactId: candidate.id,
      targetMode: "active" as const,
      reason: "passed evaluation",
    };

    await expect(controller.promote(request)).rejects.toThrow("metadata store unavailable");

    expect(runtime.observed()).toBe(baseline.id);
    expect(runtime.pointer.read()?.artifactId).toBe(baseline.id);
    expect(runtime.journal.load("metadata-failure")).toMatchObject({
      outcome: "failed",
      failureCode: "metadata_failed",
    });
    const replay = await controller.promote(request);
    expect(replay.runtime).toMatchObject({
      outcome: "failed",
      failureCode: "metadata_failed",
      idempotentReplay: true,
    });
    expect(artifacts.get(candidate.id)?.activationState).toBe("candidate");
  });
});

function runtimeFixture(initialArtifactId: string) {
  let observed: string | null = initialArtifactId;
  const deployments = new Map<string, RuntimeActivationTargetMode>([
    [initialArtifactId, "active"],
  ]);
  const pointer = memoryPointer(initialArtifactId);
  const journal = new InMemoryRuntimeActivationJournalStore();
  const driver: RuntimeActivationDriver = {
    beginActivation: async (input) => ({
      apply: async () => undefined,
      validate: async () => undefined,
      activate: async () => undefined,
      drainPrior: async () => undefined,
      cutover: async () => {
        deployments.set(input.candidateArtifactId, input.targetMode);
        if (input.targetMode === "active") observed = input.candidateArtifactId;
      },
      disposePrior: async () => {
        if (input.targetMode === "active" && input.priorArtifactId) {
          deployments.delete(input.priorArtifactId);
        }
      },
      abort: async () => {
        deployments.delete(input.candidateArtifactId);
        observed = input.priorArtifactId;
      },
    }),
    rollback: async ({ candidateArtifactId, baselineArtifactId }) => {
      deployments.delete(candidateArtifactId);
      observed = baselineArtifactId;
      if (baselineArtifactId) deployments.set(baselineArtifactId, "active");
    },
    restore: async (artifactId) => {
      observed = artifactId;
      if (artifactId) deployments.set(artifactId, "active");
    },
    observedArtifactId: () => observed,
    isActivated: (artifactId, mode) => deployments.get(artifactId) === mode,
  };
  return {
    pointer,
    journal,
    observed: () => observed,
    supervisor: new RuntimeActivationSupervisor({
      journal,
      pointer,
      driver,
      now: monotonicNow(),
    }),
  };
}

function fakeRegistry(artifacts: Map<ArtifactId, Artifact>): Registry {
  const registry = {
    cwd: "/registry",
    loadArtifact: vi.fn((id: ArtifactId) => {
      const value = artifacts.get(id);
      if (!value) throw new Error("artifact missing");
      return value;
    }),
    getActive: vi.fn((scenario, actuatorType, environmentTag) =>
      [...artifacts.values()].find((value) =>
        value.scenario === scenario
        && value.actuatorType === actuatorType
        && value.environmentTag === environmentTag
        && value.activationState === "active",
      ) ?? null),
    appendPromotionEvent: vi.fn((id: ArtifactId, event: PromotionEvent) => {
      const current = artifacts.get(id);
      if (!current) throw new Error("artifact missing");
      const next = appendPromotionEvent(current, event);
      artifacts.set(id, next);
      if (event.to === "active") {
        for (const [otherId, other] of artifacts) {
          if (
            otherId !== id
            && other.scenario === next.scenario
            && other.actuatorType === next.actuatorType
            && other.environmentTag === next.environmentTag
            && other.activationState === "active"
          ) {
            artifacts.set(otherId, appendPromotionEvent(other, {
              from: "active",
              to: "deprecated",
              reason: `superseded by ${id}`,
              timestamp: event.timestamp,
            }));
          }
        }
      }
      return next;
    }),
  };
  return registry as unknown as Registry;
}

function artifact(id: string): Artifact {
  const artifactId = parseArtifactId(id);
  const scenario = parseScenario("grid_ctf");
  const payloadHash = parseContentHash(`sha256:${"a".repeat(64)}`);
  if (!artifactId || !scenario || !payloadHash) throw new Error("invalid test fixture");
  return createArtifact({
    id: artifactId,
    actuatorType: "prompt-patch",
    scenario,
    payloadHash,
    provenance: {
      authorType: "human",
      authorId: "test",
      parentArtifactIds: [],
      createdAt: "2026-08-14T00:00:00.000Z",
    },
  });
}

function memoryPointer(initial: string | null): RuntimeActivationPointerStore {
  let pointer: RuntimeActivationPointer | null = initial
    ? { artifactId: initial, asOf: "initial" }
    : null;
  return {
    read: () => pointer,
    write: (next) => {
      pointer = { ...next };
    },
    clear: () => {
      pointer = null;
    },
  };
}

function monotonicNow(): () => string {
  let tick = 0;
  return () => `2026-08-14T00:01:${String(tick++).padStart(2, "0")}.000Z`;
}
