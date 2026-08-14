import { describe, expect, it, vi } from "vitest";

import {
  InMemoryRuntimeActivationJournalStore,
  RuntimeActivationSupervisor,
  RuntimeComponentGraphActivationDriver,
  type RuntimeActivationDriver,
  type RuntimeActivationJournalRecord,
  type RuntimeActivationPointer,
  type RuntimeActivationPointerStore,
} from "../src/control-plane/activation/index.js";
import {
  defineRuntimeCapability,
  provideRuntimeCapability,
  type RuntimeComponentManifest,
} from "../src/runtimes/component-graph.js";
import type { RuntimeEffectPolicy } from "../src/runtimes/effect-policy.js";

describe("RuntimeActivationSupervisor", () => {
  it("activates, cuts over, points, and disposes the prior graph transactionally", async () => {
    const order: string[] = [];
    const fixture = await graphFixture(order);
    order.length = 0;

    const result = await fixture.supervisor.activate({
      transactionId: "activate-candidate-1",
      candidateArtifactId: "candidate",
      targetMode: "active",
    });

    expect(result).toMatchObject({
      outcome: "succeeded",
      activeArtifactId: "candidate",
      idempotentReplay: false,
    });
    expect(fixture.pointer.read()?.artifactId).toBe("candidate");
    expect(order).toEqual([
      "apply:candidate",
      "candidate:provider:activate",
      "candidate:consumer:activate:candidate@1",
      "drain:baseline",
      "pointer:candidate",
      "baseline:consumer:dispose",
      "baseline:provider:dispose",
    ]);
    expect(fixture.journal.load("activate-candidate-1")?.entries.map((entry) => entry.stage))
      .toEqual([
        "staged",
        "applying",
        "applied",
        "validating",
        "validated",
        "activating",
        "activated",
        "draining",
        "drained",
        "cutting_over",
        "runtime_cutover",
        "pointer_cutover",
        "disposing_prior",
        "committed",
      ]);

    order.length = 0;
    const replay = await fixture.supervisor.activate({
      transactionId: "activate-candidate-1",
      candidateArtifactId: "candidate",
      targetMode: "active",
    });
    expect(replay).toMatchObject({ outcome: "succeeded", idempotentReplay: true });
    expect(order).toEqual([]);
  });

  it("unwinds a failed candidate and leaves the prior runtime and pointer active", async () => {
    const order: string[] = [];
    const fixture = await graphFixture(order, { failingArtifactId: "broken" });
    order.length = 0;

    const result = await fixture.supervisor.activate({
      transactionId: "activate-broken",
      candidateArtifactId: "broken",
      targetMode: "active",
    });

    expect(result).toMatchObject({
      outcome: "failed",
      activeArtifactId: "baseline",
      failureCode: "activation_failed",
    });
    expect(fixture.pointer.read()?.artifactId).toBe("baseline");
    expect(order).toContain("broken:partial:dispose");
    expect(order).toContain("rollback:broken:baseline");
    expect(order).not.toContain("baseline:provider:dispose");
    expect(fixture.journal.load("activate-broken")).toMatchObject({
      stage: "failed",
      outcome: "failed",
      failureCode: "activation_failed",
    });
    expect(JSON.stringify(fixture.journal.list())).not.toContain("candidate-private-error");
  });

  it("keeps shadow activation beside the baseline and denies irreversible staging effects", async () => {
    const order: string[] = [];
    const fixture = await graphFixture(order, { irreversibleArtifactId: "unsafe" });
    order.length = 0;

    const shadow = await fixture.supervisor.activate({
      transactionId: "shadow-safe",
      candidateArtifactId: "shadow",
      targetMode: "shadow",
    });
    expect(shadow).toMatchObject({ outcome: "succeeded", activeArtifactId: "baseline" });
    expect(fixture.driver.isActivated("shadow", "shadow")).toBe(true);
    expect(fixture.pointer.read()?.artifactId).toBe("baseline");
    expect(order).not.toContain("baseline:provider:dispose");

    const denied = await fixture.supervisor.activate({
      transactionId: "shadow-unsafe",
      candidateArtifactId: "unsafe",
      targetMode: "shadow",
    });
    expect(denied).toMatchObject({
      outcome: "failed",
      failureCode: "activation_failed",
      activeArtifactId: "baseline",
    });
    expect(fixture.driver.isActivated("unsafe", "shadow")).toBe(false);
    expect(fixture.pointer.read()?.artifactId).toBe("baseline");
  });

  it("rolls back through the live driver, restores the baseline, and is idempotent", async () => {
    const order: string[] = [];
    const fixture = await graphFixture(order);
    await fixture.supervisor.activate({
      transactionId: "activate-for-rollback",
      candidateArtifactId: "candidate",
      targetMode: "active",
    });
    order.length = 0;

    const result = await fixture.supervisor.rollback({
      transactionId: "rollback-candidate",
      baselineArtifactId: "baseline",
    });

    expect(result).toMatchObject({ outcome: "succeeded", activeArtifactId: "baseline" });
    expect(order).toEqual([
      "rollback:candidate:baseline",
      "drain:candidate",
      "baseline:provider:activate",
      "baseline:consumer:activate:baseline@1",
      "candidate:consumer:dispose",
      "candidate:provider:dispose",
      "pointer:baseline",
    ]);
    expect(fixture.pointer.read()?.artifactId).toBe("baseline");

    order.length = 0;
    const replay = await fixture.supervisor.rollback({
      transactionId: "rollback-candidate",
      baselineArtifactId: "baseline",
    });
    expect(replay.idempotentReplay).toBe(true);
    expect(order).toEqual([]);
  });

  it("removes a shadow deployment without moving the active pointer", async () => {
    const order: string[] = [];
    const fixture = await graphFixture(order);
    await fixture.supervisor.activate({
      transactionId: "activate-shadow",
      candidateArtifactId: "shadow",
      targetMode: "shadow",
    });
    order.length = 0;

    const result = await fixture.supervisor.rollback({
      transactionId: "rollback-shadow",
      candidateArtifactId: "shadow",
      baselineArtifactId: "baseline",
    });

    expect(result).toMatchObject({ outcome: "succeeded", activeArtifactId: "baseline" });
    expect(fixture.driver.isActivated("shadow", "shadow")).toBe(false);
    expect(fixture.pointer.read()?.artifactId).toBe("baseline");
    expect(order).toEqual([
      "rollback:shadow:baseline",
      "drain:shadow",
      "shadow:consumer:dispose",
      "shadow:provider:dispose",
    ]);
  });

  it("recovers an interrupted cutover deterministically to the journal baseline", async () => {
    const journal = new InMemoryRuntimeActivationJournalStore();
    journal.save(interruptedRecord());
    const pointer = memoryPointer("baseline");
    let observed: string | null = "candidate";
    const rollback = vi.fn(async () => {
      observed = "baseline";
    });
    const driver: RuntimeActivationDriver = {
      beginActivation: vi.fn(),
      rollback,
      restore: vi.fn(async (artifactId) => {
        observed = artifactId;
      }),
      observedArtifactId: () => observed,
      isActivated: (artifactId, mode) => mode === "active" && observed === artifactId,
    };
    const supervisor = new RuntimeActivationSupervisor({
      journal,
      pointer,
      driver,
      now: monotonicNow(),
    });

    const status = await supervisor.recover();

    expect(rollback).toHaveBeenCalledWith({
      transactionId: "interrupted-cutover",
      candidateArtifactId: "candidate",
      baselineArtifactId: "baseline",
    });
    expect(status).toMatchObject({
      pointerArtifactId: "baseline",
      observedArtifactId: "baseline",
      converged: true,
      unfinishedTransactionIds: [],
    });
    expect(journal.load("interrupted-cutover")?.outcome).toBe("recovered");
  });

  it("repairs stale runtime state to the durable pointer and records the repair", async () => {
    const journal = new InMemoryRuntimeActivationJournalStore();
    const pointer = memoryPointer("baseline");
    let observed: string | null = "stale-candidate";
    const restore = vi.fn(async (artifactId: string | null) => {
      observed = artifactId;
    });
    const driver: RuntimeActivationDriver = {
      beginActivation: vi.fn(),
      rollback: vi.fn(),
      restore,
      observedArtifactId: () => observed,
      isActivated: (artifactId, mode) => mode === "active" && observed === artifactId,
    };
    const supervisor = new RuntimeActivationSupervisor({
      journal,
      pointer,
      driver,
      now: monotonicNow(),
    });

    const before = await supervisor.status();
    const repaired = await supervisor.recover();

    expect(before.converged).toBe(false);
    expect(restore).toHaveBeenCalledWith("baseline");
    expect(repaired.converged).toBe(true);
    expect(journal.list()).toMatchObject([{
      operation: "repair",
      candidateArtifactId: "baseline",
      priorArtifactId: "stale-candidate",
      outcome: "succeeded",
    }]);
  });

  it("surfaces provider disposal failure as durable divergence until repair is acknowledged", async () => {
    const order: string[] = [];
    const fixture = await graphFixture(order, { failingDisposerArtifactId: "baseline" });
    order.length = 0;

    const result = await fixture.supervisor.activate({
      transactionId: "dispose-failure",
      candidateArtifactId: "candidate",
      targetMode: "active",
    });

    expect(result).toMatchObject({
      outcome: "failed",
      activeArtifactId: "candidate",
      failureCode: "restore_failed",
    });
    expect(fixture.pointer.read()?.artifactId).toBe("candidate");
    expect(fixture.journal.load("dispose-failure")?.outcome).toBe("diverged");
    expect((await fixture.supervisor.status()).divergentTransactionIds)
      .toEqual(["dispose-failure"]);

    fixture.driver.acknowledgeCleanupRepair("baseline");
    const recovered = await fixture.supervisor.recover();
    expect(recovered).toMatchObject({
      pointerArtifactId: "baseline",
      observedArtifactId: "baseline",
      converged: true,
    });
    expect(fixture.journal.load("dispose-failure")?.outcome).toBe("recovered");
  });
});

async function graphFixture(
  order: string[],
  options: {
    failingArtifactId?: string;
    irreversibleArtifactId?: string;
    failingDisposerArtifactId?: string;
  } = {},
) {
  const service = defineRuntimeCapability<string>("runtime.service");
  const policies = new Map<string, RuntimeEffectPolicy>();
  const manifests = (
    artifactId: string,
    effectPolicy: RuntimeEffectPolicy,
  ): RuntimeComponentManifest[] => {
    policies.set(artifactId, effectPolicy);
    if (artifactId === options.failingArtifactId) {
      return [{
        id: `${artifactId}:broken`,
        instanceId: `${artifactId}:broken@1`,
        activate: ({ scope }) => {
          scope.defer(() => {
            order.push(`${artifactId}:partial:dispose`);
          });
          throw new Error("candidate-private-error");
        },
      }];
    }
    if (artifactId === options.irreversibleArtifactId) {
      return [{
        id: `${artifactId}:unsafe`,
        instanceId: `${artifactId}:unsafe@1`,
        activate: () => {
          effectPolicy.authorize({
            effectClass: "irreversible",
            commitBoundary: "external-publish",
          });
        },
      }];
    }
    return [
      {
        id: `${artifactId}:provider`,
        instanceId: `${artifactId}@1`,
        provides: [provideRuntimeCapability(service, artifactId)],
        activate: ({ scope }) => {
          order.push(`${artifactId}:provider:activate`);
          scope.defer(() => {
            order.push(`${artifactId}:provider:dispose`);
            if (artifactId === options.failingDisposerArtifactId) {
              throw new Error("provider cleanup failed");
            }
          });
        },
      },
      {
        id: `${artifactId}:consumer`,
        instanceId: `${artifactId}:consumer@1`,
        requires: [service],
        activate: ({ providerIdentity, scope }) => {
          order.push(`${artifactId}:consumer:activate:${providerIdentity(service)}`);
          scope.defer(() => {
            order.push(`${artifactId}:consumer:dispose`);
          });
        },
      },
    ];
  };
  const driver = new RuntimeComponentGraphActivationDriver({
    resolveManifests: ({ artifactId, effectPolicy }) => manifests(artifactId, effectPolicy),
    applyArtifact: (artifactId) => {
      order.push(`apply:${artifactId}`);
    },
    rollbackArtifact: (candidate, baseline) => {
      order.push(`rollback:${candidate}:${baseline ?? "none"}`);
    },
    restoreArtifact: (artifactId) => {
      order.push(`restore:${artifactId ?? "none"}`);
    },
    drainArtifact: (artifactId) => {
      order.push(`drain:${artifactId}`);
    },
  });
  await driver.restore("baseline");
  const pointer = memoryPointer("baseline", order);
  const journal = new InMemoryRuntimeActivationJournalStore();
  const supervisor = new RuntimeActivationSupervisor({
    journal,
    pointer,
    driver,
    now: monotonicNow(),
  });
  return { driver, pointer, journal, supervisor, policies };
}

function memoryPointer(
  initial: string | null,
  order: string[] = [],
): RuntimeActivationPointerStore {
  let pointer: RuntimeActivationPointer | null = initial
    ? { artifactId: initial, asOf: "initial" }
    : null;
  return {
    read: () => pointer ? { ...pointer } : null,
    write: (next) => {
      pointer = { ...next };
      order.push(`pointer:${next.artifactId}`);
    },
    clear: () => {
      pointer = null;
      order.push("pointer:none");
    },
  };
}

function monotonicNow(): () => string {
  let tick = 0;
  return () => `2026-08-14T00:00:${String(tick++).padStart(2, "0")}.000Z`;
}

function interruptedRecord(): RuntimeActivationJournalRecord {
  return {
    schemaVersion: 1,
    transactionId: "interrupted-cutover",
    operation: "activate",
    candidateArtifactId: "candidate",
    priorArtifactId: "baseline",
    targetMode: "active",
    stage: "runtime_cutover",
    outcome: "in_progress",
    entries: [
      {
        sequence: 0,
        stage: "runtime_cutover",
        outcome: "succeeded",
        timestamp: "2026-08-14T00:00:00.000Z",
      },
    ],
    createdAt: "2026-08-14T00:00:00.000Z",
    updatedAt: "2026-08-14T00:00:00.000Z",
  };
}
