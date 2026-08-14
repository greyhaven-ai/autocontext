import { describe, expect, it, vi } from "vitest";

import {
  RuntimeComponentGraph,
  defineRuntimeCapability,
  provideRuntimeCapability,
  type RuntimeComponentManifest,
} from "../src/runtimes/component-graph.js";
import { createRuntimeSessionComponentGraphEventSink } from "../src/session/runtime-component-graph-events.js";
import { normalizeBackgroundSessionTimeline } from "../src/session/background-session-events.js";
import {
  RuntimeSessionEventLog,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";
import { buildRuntimeSessionTimeline } from "../src/session/runtime-session-timeline.js";

describe("RuntimeComponentGraph", () => {
  it("keeps a consumer waiting until its missing provider is added", async () => {
    const endpoint = defineRuntimeCapability<string>("service.endpoint");
    const observed: string[] = [];
    const consumer: RuntimeComponentManifest = {
      id: "consumer",
      instanceId: "consumer@1",
      requires: [endpoint],
      activate: ({ get }) => {
        observed.push(get(endpoint));
      },
    };
    const graph = new RuntimeComponentGraph();

    const waiting = await graph.reconcile([consumer]);
    expect(waiting.components).toMatchObject([{
      componentId: "consumer",
      state: "waiting",
      reason: "missing_requirement",
      capabilityId: "service.endpoint",
    }]);
    expect(observed).toEqual([]);

    const provider: RuntimeComponentManifest = {
      id: "provider",
      instanceId: "provider@1",
      provides: [provideRuntimeCapability(endpoint, "https://one.example")],
      activate: () => undefined,
    };
    const active = await graph.reconcile([consumer, provider]);

    expect(observed).toEqual(["https://one.example"]);
    expect(active.components.map((component) => [component.componentId, component.state])).toEqual([
      ["consumer", "active"],
      ["provider", "active"],
    ]);
  });

  it("does not expose capabilities a component omitted from its manifest", async () => {
    const declared = defineRuntimeCapability<string>("service.declared");
    const ambient = defineRuntimeCapability<string>("service.ambient");
    const graph = new RuntimeComponentGraph();

    const snapshot = await graph.reconcile([
      {
        id: "providers",
        instanceId: "providers@1",
        provides: [
          provideRuntimeCapability(declared, "declared"),
          provideRuntimeCapability(ambient, "ambient"),
        ],
        activate: () => undefined,
      },
      {
        id: "consumer",
        instanceId: "consumer@1",
        requires: [declared],
        activate: ({ get }) => {
          expect(get(declared)).toBe("declared");
          get(ambient);
        },
      },
    ]);

    expect(snapshot.components.find((component) => component.componentId === "consumer"))
      .toMatchObject({ state: "failed", reason: "activation_failed" });
  });

  it("rejects duplicate providers and cycles before changing an active graph", async () => {
    const a = defineRuntimeCapability<string>("capability.a");
    const b = defineRuntimeCapability<string>("capability.b");
    const stableActivate = vi.fn();
    const stable: RuntimeComponentManifest = {
      id: "stable",
      instanceId: "stable@1",
      provides: [provideRuntimeCapability(a, "a")],
      activate: stableActivate,
    };
    const graph = new RuntimeComponentGraph();
    await graph.reconcile([stable]);

    await expect(graph.reconcile([
      stable,
      {
        id: "duplicate",
        instanceId: "duplicate@1",
        provides: [provideRuntimeCapability(a, "also-a")],
        activate: () => undefined,
      },
    ])).rejects.toMatchObject({ code: "duplicate_provider", capabilityId: "capability.a" });

    await expect(graph.reconcile([
      {
        id: "cycle-a",
        instanceId: "cycle-a@1",
        requires: [b],
        provides: [provideRuntimeCapability(a, "a")],
        activate: () => undefined,
      },
      {
        id: "cycle-b",
        instanceId: "cycle-b@1",
        requires: [a],
        provides: [provideRuntimeCapability(b, "b")],
        activate: () => undefined,
      },
    ])).rejects.toMatchObject({ code: "dependency_cycle" });

    expect(graph.snapshot().components).toMatchObject([{
      componentId: "stable",
      state: "active",
    }]);
    expect(stableActivate).toHaveBeenCalledOnce();
  });

  it("marks a provider unavailable, drains dependents first, and preserves unrelated components", async () => {
    const endpoint = defineRuntimeCapability<string>("service.endpoint");
    const order: string[] = [];
    const unrelatedActivate = vi.fn(({ scope }) => {
      order.push("unrelated:activate");
      scope.defer(() => {
        order.push("unrelated:dispose");
      });
    });
    const consumer: RuntimeComponentManifest = {
      id: "consumer",
      instanceId: "consumer@1",
      requires: [endpoint],
      activate: ({ get, providerIdentity, scope }) => {
        order.push(`consumer:activate:${providerIdentity(endpoint)}:${get(endpoint)}`);
        scope.defer(() => {
          order.push("consumer:dispose");
        });
      },
    };
    const unrelated: RuntimeComponentManifest = {
      id: "unrelated",
      instanceId: "unrelated@1",
      activate: unrelatedActivate,
    };
    const provider = (id: string, instanceId: string): RuntimeComponentManifest => ({
      id,
      instanceId,
      provides: [provideRuntimeCapability(endpoint, "equal-value")],
      activate: ({ scope }) => {
        order.push(`${id}:activate`);
        scope.defer(() => {
          order.push(`${id}:dispose`);
        });
      },
    });
    const graph = new RuntimeComponentGraph({
      eventSink: {
        onRuntimeComponentGraphEvent: (event) => {
          if (event.operation === "provider_unavailable") {
            order.push(`unavailable:${event.componentId}`);
          }
        },
      },
    });

    await graph.reconcile([consumer, provider("provider-one", "provider-one@1"), unrelated]);
    order.length = 0;
    const replaced = await graph.reconcile([
      consumer,
      provider("provider-two", "provider-two@1"),
      unrelated,
    ]);

    expect(order).toEqual([
      "unavailable:provider-one",
      "consumer:dispose",
      "provider-one:dispose",
      "provider-two:activate",
      "consumer:activate:provider-two@1:equal-value",
    ]);
    expect(unrelatedActivate).toHaveBeenCalledOnce();
    expect(replaced.providers).toEqual([{
      capabilityId: "service.endpoint",
      componentId: "provider-two",
      instanceId: "provider-two@1",
      available: true,
    }]);
    expect(replaced.components.find((component) => component.componentId === "consumer"))
      .toMatchObject({
        state: "active",
        providerInstanceIds: { "service.endpoint": "provider-two@1" },
      });
  });

  it("removes dependents before their provider and leaves them waiting", async () => {
    const service = defineRuntimeCapability<number>("service.number");
    const order: string[] = [];
    const provider: RuntimeComponentManifest = {
      id: "provider",
      instanceId: "provider@1",
      provides: [provideRuntimeCapability(service, 7)],
      activate: ({ scope }) => {
        scope.defer(() => {
          order.push("provider");
        });
      },
    };
    const consumer: RuntimeComponentManifest = {
      id: "consumer",
      instanceId: "consumer@1",
      requires: [service],
      activate: ({ scope }) => {
        scope.defer(() => {
          order.push("consumer");
        });
      },
    };
    const graph = new RuntimeComponentGraph();
    await graph.reconcile([provider, consumer]);

    const removed = await graph.reconcile([consumer]);

    expect(order).toEqual(["consumer", "provider"]);
    expect(removed.providers).toEqual([]);
    expect(removed.components).toMatchObject([{
      componentId: "consumer",
      state: "waiting",
      reason: "missing_requirement",
    }]);
  });

  it("contains partial activation failure and still activates unrelated components", async () => {
    const service = defineRuntimeCapability<string>("service.partial");
    const cleanup = vi.fn();
    const unrelated = vi.fn();
    const graph = new RuntimeComponentGraph();

    const snapshot = await graph.reconcile([
      {
        id: "broken-provider",
        instanceId: "broken-provider@1",
        provides: [provideRuntimeCapability(service, "value")],
        activate: ({ scope }) => {
          scope.defer(cleanup);
          throw new Error("candidate-secret-error");
        },
      },
      {
        id: "dependent",
        instanceId: "dependent@1",
        requires: [service],
        activate: () => {
          throw new Error("must not activate");
        },
      },
      {
        id: "unrelated",
        instanceId: "unrelated@1",
        activate: unrelated,
      },
    ]);

    expect(cleanup).toHaveBeenCalledOnce();
    expect(unrelated).toHaveBeenCalledOnce();
    expect(snapshot.components).toMatchObject([
      { componentId: "broken-provider", state: "failed", reason: "activation_failed" },
      { componentId: "dependent", state: "waiting", reason: "provider_inactive" },
      { componentId: "unrelated", state: "active" },
    ]);
  });

  it("fails closed after provider cleanup fails until the supervisor acknowledges repair", async () => {
    const service = defineRuntimeCapability<string>("service.cleanup");
    const replacementActivate = vi.fn();
    const original: RuntimeComponentManifest = {
      id: "original",
      instanceId: "original@1",
      provides: [provideRuntimeCapability(service, "same")],
      activate: ({ scope }) => {
        scope.defer(() => {
          throw new Error("cleanup failed");
        });
      },
    };
    const replacement: RuntimeComponentManifest = {
      id: "replacement",
      instanceId: "replacement@1",
      provides: [provideRuntimeCapability(service, "same")],
      activate: replacementActivate,
    };
    const graph = new RuntimeComponentGraph();
    await graph.reconcile([original]);

    const blocked = await graph.reconcile([replacement]);
    expect(replacementActivate).not.toHaveBeenCalled();
    expect(blocked.blockedCapabilities).toEqual(["service.cleanup"]);
    expect(blocked.blockedComponentIds).toEqual(["original"]);
    expect(blocked.components).toMatchObject([{
      componentId: "replacement",
      state: "failed",
      reason: "provider_cleanup_failed",
    }]);

    graph.acknowledgeProviderCleanup(service);
    const repaired = await graph.reconcile([replacement]);
    expect(replacementActivate).toHaveBeenCalledOnce();
    expect(repaired.providers[0]?.instanceId).toBe("replacement@1");
    expect(repaired.blockedComponentIds).toEqual([]);
  });

  it("blocks a leaf replacement after cleanup fails until component repair is acknowledged", async () => {
    const replacementActivate = vi.fn();
    const original: RuntimeComponentManifest = {
      id: "leaf",
      instanceId: "leaf@1",
      activate: ({ scope }) => {
        scope.defer(() => {
          throw new Error("leaf cleanup failed");
        });
      },
    };
    const replacement: RuntimeComponentManifest = {
      id: "leaf",
      instanceId: "leaf@2",
      activate: replacementActivate,
    };
    const graph = new RuntimeComponentGraph();
    await graph.reconcile([original]);

    const blocked = await graph.reconcile([replacement]);
    expect(replacementActivate).not.toHaveBeenCalled();
    expect(blocked.blockedCapabilities).toEqual([]);
    expect(blocked.blockedComponentIds).toEqual(["leaf"]);
    expect(blocked.components).toMatchObject([{
      componentId: "leaf",
      state: "failed",
      reason: "cleanup_failed",
    }]);

    graph.acknowledgeComponentCleanup("leaf");
    const repaired = await graph.reconcile([replacement]);
    expect(replacementActivate).toHaveBeenCalledOnce();
    expect(repaired.blockedComponentIds).toEqual([]);
  });

  it("blocks a leaf retry when partial-activation unwind fails", async () => {
    const retry = vi.fn();
    const graph = new RuntimeComponentGraph();
    const failed = await graph.reconcile([{
      id: "leaf",
      instanceId: "leaf@1",
      activate: ({ scope }) => {
        scope.defer(() => {
          throw new Error("partial cleanup failed");
        });
        throw new Error("activation failed");
      },
    }]);

    expect(failed.blockedComponentIds).toEqual(["leaf"]);
    expect(failed.blockedCapabilities).toEqual([]);
    const blocked = await graph.reconcile([{
      id: "leaf",
      instanceId: "leaf@2",
      activate: retry,
    }]);
    expect(retry).not.toHaveBeenCalled();
    expect(blocked.components).toMatchObject([{
      componentId: "leaf",
      state: "failed",
      reason: "cleanup_failed",
    }]);
  });

  it("serializes async races and converges to the latest requested graph", async () => {
    const service = defineRuntimeCapability<string>("service.race");
    const started = deferred<void>();
    const release = deferred<void>();
    const slow: RuntimeComponentManifest = {
      id: "slow",
      instanceId: "slow@1",
      provides: [provideRuntimeCapability(service, "slow")],
      activate: async () => {
        started.resolve();
        await release.promise;
      },
    };
    const fast: RuntimeComponentManifest = {
      id: "fast",
      instanceId: "fast@1",
      provides: [provideRuntimeCapability(service, "fast")],
      activate: () => undefined,
    };
    const graph = new RuntimeComponentGraph();

    const first = graph.reconcile([slow]);
    await started.promise;
    const latest = graph.reconcile([fast]);
    release.resolve();
    const firstSnapshot = await first;
    const latestSnapshot = await latest;

    expect(firstSnapshot.revision).toBe(1);
    expect(firstSnapshot.transitioning).toBe(true);
    expect(latestSnapshot.revision).toBe(2);
    expect(latestSnapshot.transitioning).toBe(false);
    expect(graph.snapshot().providers).toMatchObject([{
      componentId: "fast",
      instanceId: "fast@1",
    }]);
  });

  it("writes sanitized graph diagnostics to runtime-session events", async () => {
    const log = RuntimeSessionEventLog.create({ sessionId: "session-graph" });
    const graph = new RuntimeComponentGraph({
      eventSink: createRuntimeSessionComponentGraphEventSink(log),
    });

    await graph.reconcile([{
      id: "broken",
      instanceId: "broken@1",
      activate: () => {
        throw new Error("private-candidate-token");
      },
    }]);

    expect(log.events.every(
      (event) => event.eventType === RuntimeSessionEventType.COMPONENT_GRAPH,
    )).toBe(true);
    expect(log.events[0]?.payload).toMatchObject({
      revision: 1,
      operation: "component_failed",
      outcome: "failed",
      componentId: "broken",
      reason: "activation_failed",
    });
    expect(JSON.stringify(log.toJSON())).not.toContain("private-candidate-token");
    expect(RuntimeSessionEventLog.fromJSON(log.toJSON()).events[0]?.eventType)
      .toBe(RuntimeSessionEventType.COMPONENT_GRAPH);
    expect(normalizeBackgroundSessionTimeline(log)[0]).toMatchObject({
      title: "Runtime component graph",
      status: "failed",
      payload_summary: {
        component_id: "broken",
        reason: "activation_failed",
      },
    });
    expect(buildRuntimeSessionTimeline(log).items[0]).toMatchObject({
      event_type: "component_graph",
      details: {
        componentId: "broken",
        reason: "activation_failed",
      },
    });
  });
});

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}
