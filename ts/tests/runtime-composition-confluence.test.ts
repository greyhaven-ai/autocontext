import fc from "fast-check";
import { describe, expect, it } from "vitest";

import { ExtensionAPI, HookBus, HookEvents } from "../src/extensions/hooks.js";
import {
  RuntimeComponentGraph,
  defineRuntimeCapability,
  provideRuntimeCapability,
  type RuntimeComponentManifest,
} from "../src/runtimes/component-graph.js";
import {
  RuntimeCompositionInventory,
  assertRuntimeCompositionQuiescent,
  captureRuntimeCompositionSnapshot,
  compareRuntimeCompositionSnapshots,
  type RuntimeCompositionResourceKind,
} from "../src/runtimes/composition-observability.js";

type ConfigurationName = "empty" | "waiting" | "v1" | "v2";

const CI_RUNS = readPositiveInteger(process.env.AUTOCTX_CONFLUENCE_RUNS, 24);

describe("runtime composition confluence", () => {
  it("matches a clean boot for seeded dynamic histories with the same final graph", async () => {
    await fc.assert(fc.asyncProperty(
      fc.array(fc.constantFrom<ConfigurationName>("empty", "waiting", "v1", "v2"), {
        minLength: 0,
        maxLength: 14,
      }),
      async (prefix) => {
        const history = [...prefix, "v2" as const];
        const dynamic = compositionFixture();
        for (const configuration of history) {
          await dynamic.graph.reconcile(dynamic.manifests(configuration));
        }
        const clean = compositionFixture();
        await clean.graph.reconcile(clean.manifests("v2"));

        const dynamicSnapshot = captureRuntimeCompositionSnapshot({
          graph: dynamic.graph.snapshot(),
          inventory: dynamic.inventory,
          activePointer: "artifact-v2",
        });
        const cleanSnapshot = captureRuntimeCompositionSnapshot({
          graph: clean.graph.snapshot(),
          inventory: clean.inventory,
          activePointer: "artifact-v2",
        });

        assertRuntimeCompositionQuiescent(dynamicSnapshot);
        assertRuntimeCompositionQuiescent(cleanSnapshot);
        expect(compareRuntimeCompositionSnapshots(dynamicSnapshot, cleanSnapshot))
          .toMatchObject({ equivalent: true });

        const dynamicHookCalls = dynamic.hookCalls;
        dynamic.bus.emit(HookEvents.CONTEXT);
        expect(dynamic.hookCalls - dynamicHookCalls).toBe(1);
        const cleanHookCalls = clean.hookCalls;
        clean.bus.emit(HookEvents.CONTEXT);
        expect(clean.hookCalls - cleanHookCalls).toBe(1);

        await dynamic.disposeAndAssertNoLeaks();
        await clean.disposeAndAssertNoLeaks();
      },
    ), { seed: 962, numRuns: CI_RUNS });
  });

  it("keeps unrelated components stable during local provider recomposition", async () => {
    const fixture = compositionFixture();
    await fixture.graph.reconcile(fixture.manifests("v1"));
    await fixture.graph.reconcile(fixture.manifests("v2"));

    expect(fixture.activations.get("unrelated")).toBe(1);
    expect(fixture.disposals.get("unrelated")).toBeUndefined();
    expect(fixture.activations.get("consumer")).toBe(2);
    expect(fixture.disposals.get("consumer")).toBe(1);
    await fixture.disposeAndAssertNoLeaks();
  });

  it("compares compensatable effects by declared equivalence and excludes emissions", async () => {
    const dynamic = compositionFixture();
    await dynamic.graph.reconcile(dynamic.manifests("v1"));
    await dynamic.graph.reconcile(dynamic.manifests("empty"));
    await dynamic.graph.reconcile(dynamic.manifests("v2"));
    const clean = compositionFixture();
    await clean.graph.reconcile(clean.manifests("v2"));

    const dynamicSnapshot = captureRuntimeCompositionSnapshot({
      graph: dynamic.graph.snapshot(),
      inventory: dynamic.inventory,
      activePointer: "artifact-v2",
    });
    const cleanSnapshot = captureRuntimeCompositionSnapshot({
      graph: clean.graph.snapshot(),
      inventory: clean.inventory,
      activePointer: "artifact-v2",
    });

    expect(dynamic.externalEmissions.length).toBeGreaterThan(clean.externalEmissions.length);
    expect(dynamicSnapshot.ownedResources).toContain(
      "consumer:equivalent:one reservation for the active provider",
    );
    expect(dynamicSnapshot.excludedIrreversibleEffects).toBe(1);
    expect(compareRuntimeCompositionSnapshots(dynamicSnapshot, cleanSnapshot).equivalent).toBe(true);
    await dynamic.disposeAndAssertNoLeaks();
    await clean.disposeAndAssertNoLeaks();
  });

  it("fails quiescence checks on live lifecycle errors and blocked capabilities", async () => {
    const fixture = compositionFixture();
    fixture.inventory.recordLifecycleError("consumer", "stale_subscription");
    await fixture.graph.reconcile(fixture.manifests("v2"));
    const snapshot = captureRuntimeCompositionSnapshot({
      graph: fixture.graph.snapshot(),
      inventory: fixture.inventory,
      activePointer: "artifact-v2",
    });

    expect(() => assertRuntimeCompositionQuiescent(snapshot)).toThrow(
      "unresolved lifecycle errors",
    );
    fixture.inventory.resolveLifecycleError("consumer", "stale_subscription");
    await fixture.disposeAndAssertNoLeaks();
  });

  it("fails quiescence when cleanup of a capability-free component is unresolved", async () => {
    const graph = new RuntimeComponentGraph();
    const inventory = new RuntimeCompositionInventory();
    await graph.reconcile([{
      id: "leaf",
      instanceId: "leaf@1",
      activate: ({ scope }) => {
        scope.defer(() => {
          throw new Error("leaf cleanup failed");
        });
      },
    }]);
    await graph.reconcile([]);

    const snapshot = captureRuntimeCompositionSnapshot({ graph: graph.snapshot(), inventory });
    expect(snapshot.blockedCapabilities).toEqual([]);
    expect(snapshot.blockedComponents).toEqual(["leaf"]);
    expect(() => assertRuntimeCompositionQuiescent(snapshot)).toThrow("blocked components");
  });
});

function compositionFixture() {
  const capability = defineRuntimeCapability<string>("runtime.endpoint");
  const graph = new RuntimeComponentGraph();
  const inventory = new RuntimeCompositionInventory();
  const bus = new HookBus();
  const live = new Map<RuntimeCompositionResourceKind, Set<string>>();
  const activations = new Map<string, number>();
  const disposals = new Map<string, number>();
  const externalEmissions: string[] = [];
  let hookCalls = 0;
  let resourceSequence = 0;

  const count = (map: Map<string, number>, key: string): void => {
    map.set(key, (map.get(key) ?? 0) + 1);
  };
  const own = (
    scope: Parameters<RuntimeCompositionInventory["own"]>[0],
    kind: RuntimeCompositionResourceKind,
    resourceId: string,
  ): void => {
    const resources = live.get(kind) ?? new Set<string>();
    resources.add(`${scope.componentId}:${resourceId}`);
    live.set(kind, resources);
    inventory.own(scope, { kind, resourceId, effectClass: "reversible" }, () => {
      resources.delete(`${scope.componentId}:${resourceId}`);
    });
  };

  const provider = (version: "v1" | "v2"): RuntimeComponentManifest => ({
    id: "provider",
    instanceId: `provider@${version}`,
    provides: [provideRuntimeCapability(capability, version)],
    activate: ({ scope }) => {
      count(activations, "provider");
      own(scope, "subscription", "provider-events");
      scope.defer(() => count(disposals, "provider"));
    },
  });
  const consumer: RuntimeComponentManifest = {
    id: "consumer",
    instanceId: "consumer@1",
    requires: [capability],
    activate: ({ get, scope }) => {
      count(activations, "consumer");
      const version = get(capability);
      const api = new ExtensionAPI(bus, scope, inventory);
      api.on(HookEvents.CONTEXT, () => {
        hookCalls += 1;
        return undefined;
      });
      for (const [kind, id] of [
        ["tool", "inspect"],
        ["grant", "command:inspect"],
        ["subscription", "consumer-events"],
        ["timer", "heartbeat"],
        ["task", "refresh"],
        ["temporary_resource", "scratch-dir"],
      ] as const) {
        own(scope, kind, id);
      }
      inventory.own(scope, {
        kind: "resource",
        resourceId: `reservation-${version}-${resourceSequence++}`,
        effectClass: "compensatable",
        observationalEquivalence: "one reservation for the active provider",
      });
      externalEmissions.push(`published-${version}-${resourceSequence}`);
      inventory.own(scope, {
        kind: "resource",
        resourceId: `publication-${version}-${resourceSequence}`,
        effectClass: "irreversible",
      });
      scope.defer(() => count(disposals, "consumer"));
    },
  };
  const unrelated: RuntimeComponentManifest = {
    id: "unrelated",
    instanceId: "unrelated@1",
    activate: ({ scope }) => {
      count(activations, "unrelated");
      own(scope, "timer", "unrelated-heartbeat");
      scope.defer(() => count(disposals, "unrelated"));
    },
  };
  const manifests = (configuration: ConfigurationName): RuntimeComponentManifest[] => {
    switch (configuration) {
      case "empty":
        return [];
      case "waiting":
        return [consumer, unrelated];
      case "v1":
        return [provider("v1"), consumer, unrelated];
      case "v2":
        return [provider("v2"), consumer, unrelated];
    }
  };

  return {
    graph,
    inventory,
    bus,
    live,
    activations,
    disposals,
    externalEmissions,
    manifests,
    get hookCalls() {
      return hookCalls;
    },
    async disposeAndAssertNoLeaks(): Promise<void> {
      await graph.reconcile([]);
      expect(inventory.snapshot().registrations).toEqual([]);
      expect(bus.hasHandlers(HookEvents.CONTEXT)).toBe(false);
      for (const resources of live.values()) expect(resources.size).toBe(0);
    },
  };
}

function readPositiveInteger(value: string | undefined, fallback: number): number {
  if (value === undefined) return fallback;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}
