import { describe, expect, it } from "vitest";

import {
  InMemoryRuntimeActivationJournalStore,
  RuntimeActivationSupervisor,
  type RuntimeActivationDriver,
  type RuntimeActivationFailureCode,
  type RuntimeActivationJournalRecord,
  type RuntimeActivationPointer,
  type RuntimeActivationPointerStore,
} from "../src/control-plane/activation/index.js";
import {
  RuntimeComponentGraph,
  defineRuntimeCapability,
  provideRuntimeCapability,
  type RuntimeComponentManifest,
} from "../src/runtimes/component-graph.js";
import { DeterministicRuntimeTransitionScheduler } from "../src/runtimes/transition-scheduler.js";

type FaultBoundary = "apply" | "validate" | "activate" | "drain" | "cutover" | "pointer" | "dispose";

const FAILURE_CODE: Record<FaultBoundary, RuntimeActivationFailureCode> = {
  apply: "apply_failed",
  validate: "validation_failed",
  activate: "activation_failed",
  drain: "drain_failed",
  cutover: "cutover_failed",
  pointer: "pointer_failed",
  dispose: "disposal_failed",
};

const INTERRUPT_STAGES: readonly RuntimeActivationJournalRecord["stage"][] = [
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
];

describe("runtime composition fault injection", () => {
  it("recovers in bounded steps from every activation, cutover, pointer, and disposal fault", async () => {
    for (const boundary of Object.keys(FAILURE_CODE) as FaultBoundary[]) {
      const pointer = faultPointer("baseline", boundary === "pointer");
      const journal = new InMemoryRuntimeActivationJournalStore();
      const state = { observed: "baseline" as string | null, candidateActivated: false };
      const driver = faultDriver(boundary, state);
      const supervisor = new RuntimeActivationSupervisor({
        journal,
        pointer,
        driver,
        now: monotonicNow(),
      });

      const result = await supervisor.activate({
        transactionId: `fault-${boundary}`,
        candidateArtifactId: "candidate",
        targetMode: "active",
      });

      expect(result, boundary).toMatchObject({
        outcome: "failed",
        failureCode: FAILURE_CODE[boundary],
        activeArtifactId: "baseline",
      });
      expect(pointer.read()?.artifactId, boundary).toBe("baseline");
      expect(state.candidateActivated, boundary).toBe(false);
      const record = journal.load(`fault-${boundary}`);
      expect(record?.outcome, boundary).toBe("failed");
      expect(record?.entries.length, boundary).toBeLessThanOrEqual(18);
    }
  });

  it("rolls interrupted transactions at every durable boundary back to the baseline", async () => {
    for (const [index, stage] of INTERRUPT_STAGES.entries()) {
      const afterRuntimeCutover = index >= INTERRUPT_STAGES.indexOf("runtime_cutover");
      const afterPointerCutover = index >= INTERRUPT_STAGES.indexOf("pointer_cutover");
      const state = {
        observed: afterRuntimeCutover ? "candidate" : "baseline",
        candidateActivated: afterRuntimeCutover,
      };
      const pointer = faultPointer(afterPointerCutover ? "candidate" : "baseline", false);
      const journal = new InMemoryRuntimeActivationJournalStore();
      journal.save(interruptedRecord(stage, index));
      const supervisor = new RuntimeActivationSupervisor({
        journal,
        pointer,
        driver: faultDriver(undefined, state),
        now: monotonicNow(),
      });

      const status = await supervisor.recover();

      expect(status.converged, stage).toBe(true);
      expect(status.pointerArtifactId, stage).toBe("baseline");
      expect(status.observedArtifactId, stage).toBe("baseline");
      expect(status.unfinishedTransactionIds, stage).toEqual([]);
      expect(journal.load(`interrupt-${index}`)?.outcome, stage).toBe("recovered");
    }
  });

  it("uses seeded scheduling to converge async provider races to the latest request", async () => {
    for (let seed = 1; seed <= 16; seed += 1) {
      const service = defineRuntimeCapability<string>(`service.race.${seed}`);
      const started = deferred<void>();
      const release = deferred<void>();
      const graph = new RuntimeComponentGraph();
      const slow: RuntimeComponentManifest = {
        id: "provider",
        instanceId: "provider@slow",
        provides: [provideRuntimeCapability(service, "slow")],
        activate: async () => {
          started.resolve();
          await release.promise;
        },
      };
      const fast: RuntimeComponentManifest = {
        id: "provider",
        instanceId: "provider@fast",
        provides: [provideRuntimeCapability(service, "fast")],
        activate: () => undefined,
      };
      const first = graph.reconcile([slow]);
      await started.promise;
      let latest: Promise<unknown> | undefined;
      const scheduler = new DeterministicRuntimeTransitionScheduler(seed);
      scheduler.schedule("enqueue-latest", async () => {
        latest = graph.reconcile([fast]);
      });
      scheduler.schedule("release-first", async () => {
        release.resolve();
      });

      const run = await scheduler.runUntilQuiescent(4);
      await first;
      await latest;

      expect(run.steps).toBe(2);
      expect(scheduler.pendingCount).toBe(0);
      expect(graph.snapshot()).toMatchObject({
        transitioning: false,
        providers: [{ instanceId: "provider@fast" }],
      });
    }
  });

  it("replays the same cooperative interleaving for the same seed and bounds non-quiescence", async () => {
    const run = async (seed: number) => {
      const scheduler = new DeterministicRuntimeTransitionScheduler(seed);
      scheduler.schedule("alpha", async function* () {
        yield;
        yield;
      });
      scheduler.schedule("beta", async function* () {
        yield;
      });
      return scheduler.runUntilQuiescent(10);
    };

    expect((await run(962)).history).toEqual((await run(962)).history);

    const bounded = new DeterministicRuntimeTransitionScheduler(962);
    bounded.schedule("never-fast-enough", async function* () {
      while (true) yield;
    });
    await expect(bounded.runUntilQuiescent(3)).rejects.toThrow("exceeded 3 steps");
  });
});

function faultDriver(
  boundary: FaultBoundary | undefined,
  state: { observed: string | null; candidateActivated: boolean },
): RuntimeActivationDriver {
  const fail = (at: FaultBoundary): void => {
    if (boundary === at) throw new Error(`private-${at}-failure`);
  };
  return {
    beginActivation: async (input) => ({
      apply: async () => fail("apply"),
      validate: async () => fail("validate"),
      activate: async () => {
        fail("activate");
        state.candidateActivated = true;
      },
      drainPrior: async () => fail("drain"),
      cutover: async () => {
        fail("cutover");
        state.observed = input.candidateArtifactId;
      },
      disposePrior: async () => fail("dispose"),
      abort: async () => {
        state.observed = input.priorArtifactId;
        state.candidateActivated = false;
      },
    }),
    rollback: async ({ baselineArtifactId }) => {
      state.observed = baselineArtifactId;
      state.candidateActivated = false;
    },
    restore: async (artifactId) => {
      state.observed = artifactId;
      state.candidateActivated = false;
    },
    observedArtifactId: () => state.observed,
    isActivated: (artifactId, mode) =>
      mode === "active" && state.candidateActivated && state.observed === artifactId,
  };
}

function faultPointer(
  initial: string | null,
  failCandidateWrite: boolean,
): RuntimeActivationPointerStore {
  let pointer: RuntimeActivationPointer | null = initial
    ? { artifactId: initial, asOf: "initial" }
    : null;
  let failed = false;
  return {
    read: () => pointer,
    write: (next) => {
      if (failCandidateWrite && next.artifactId === "candidate" && !failed) {
        failed = true;
        throw new Error("private-pointer-failure");
      }
      pointer = { ...next };
    },
    clear: () => {
      pointer = null;
    },
  };
}

function interruptedRecord(
  stage: RuntimeActivationJournalRecord["stage"],
  index: number,
): RuntimeActivationJournalRecord {
  return {
    schemaVersion: 1,
    transactionId: `interrupt-${index}`,
    operation: "activate",
    candidateArtifactId: "candidate",
    priorArtifactId: "baseline",
    targetMode: "active",
    stage,
    outcome: "in_progress",
    entries: [{
      sequence: 0,
      stage,
      outcome: "started",
      timestamp: "2026-08-14T00:00:00.000Z",
    }],
    createdAt: "2026-08-14T00:00:00.000Z",
    updatedAt: "2026-08-14T00:00:00.000Z",
  };
}

function monotonicNow(): () => string {
  let tick = 0;
  return () => `2026-08-14T00:02:${String(tick++).padStart(2, "0")}.000Z`;
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
