import { describe, expect, it } from "vitest";

import { HookBus, HookEvents } from "../src/extensions/hooks.js";
import {
  RuntimeComponentLifecycleState,
  RuntimeComponentScope,
  activateRuntimeComponent,
  type RuntimeComponentLifecycleEvent,
} from "../src/runtimes/component-lifecycle.js";
import { createRuntimeSessionComponentLifecycleEventSink } from "../src/session/runtime-component-lifecycle-events.js";
import {
  RuntimeSessionEventLog,
  RuntimeSessionEventType,
} from "../src/session/runtime-events.js";
import { ExtensionAPI } from "../src/extensions/hooks.js";

describe("RuntimeComponentScope", () => {
  it("disposes sync and async effects in exact LIFO order at most once", async () => {
    const order: string[] = [];
    const events: RuntimeComponentLifecycleEvent[] = [];
    const scope = await activateRuntimeComponent(
      {
        componentId: "extension:metrics",
        eventSink: { onRuntimeComponentLifecycleEvent: (event) => events.push(event) },
      },
      (component) => {
        component.defer(() => {
          order.push("first-sync");
        });
        component.defer(async () => {
          await Promise.resolve();
          order.push("second-async");
        });
      },
    );

    expect(scope.state).toBe(RuntimeComponentLifecycleState.ACTIVE);
    await Promise.all([scope.dispose(), scope.dispose()]);
    await scope.dispose();

    expect(order).toEqual(["second-async", "first-sync"]);
    expect(scope.state).toBe(RuntimeComponentLifecycleState.INACTIVE);
    expect(events.map((event) => [event.previousState, event.state])).toEqual([
      ["inactive", "loading"],
      ["loading", "active"],
      ["active", "unloading"],
      ["unloading", "inactive"],
    ]);
  });

  it("unwinds every registered effect after partial activation failure", async () => {
    const order: string[] = [];
    const scope = new RuntimeComponentScope({ componentId: "extension:broken" });

    await expect(scope.activate(async (component) => {
      component.defer(() => {
        order.push("first");
      });
      component.defer(async () => {
        order.push("second");
      });
      throw new Error("activation failed");
    })).rejects.toThrow("activation failed");

    expect(order).toEqual(["second", "first"]);
    expect(scope.state).toBe(RuntimeComponentLifecycleState.FAILED);
    await scope.dispose();
    expect(order).toEqual(["second", "first"]);
  });

  it("continues cleanup after a disposer failure without retrying an inverse", async () => {
    const order: string[] = [];
    const scope = await activateRuntimeComponent(
      { componentId: "extension:cleanup-failure" },
      (component) => {
        component.defer(() => {
          order.push("first");
        });
        component.defer(() => {
          order.push("second");
          throw new Error("cleanup failed");
        });
      },
    );

    await expect(scope.dispose()).rejects.toThrow("disposal failed");
    await expect(scope.dispose()).rejects.toThrow("disposal failed");

    expect(order).toEqual(["second", "first"]);
    expect(scope.state).toBe(RuntimeComponentLifecycleState.FAILED);
  });

  it("removes hook registrations owned by a component scope", async () => {
    const bus = new HookBus();
    let calls = 0;
    const scope = await activateRuntimeComponent(
      { componentId: "extension:hook-owner" },
      (component) => {
        const api = new ExtensionAPI(bus, component);
        api.on(HookEvents.CONTEXT, () => {
          calls += 1;
          return undefined;
        });
      },
    );

    bus.emit(HookEvents.CONTEXT);
    await scope.dispose();
    bus.emit(HookEvents.CONTEXT);

    expect(calls).toBe(1);
    expect(bus.hasHandlers(HookEvents.CONTEXT)).toBe(false);
  });

  it("does not leak a hook when registration is attempted outside the scope lifecycle", () => {
    const bus = new HookBus();
    const scope = new RuntimeComponentScope({ componentId: "extension:inactive" });
    const api = new ExtensionAPI(bus, scope);

    expect(() => api.on(HookEvents.CONTEXT, () => undefined)).toThrow(
      "cannot register cleanup while inactive",
    );
    expect(bus.hasHandlers(HookEvents.CONTEXT)).toBe(false);
  });

  it("records sanitized lifecycle transitions in the runtime-session event log", async () => {
    const log = RuntimeSessionEventLog.create({ sessionId: "session-1" });
    const scope = new RuntimeComponentScope({
      componentId: "extension:safe-id",
      eventSink: createRuntimeSessionComponentLifecycleEventSink(log),
    });

    await expect(scope.activate(() => {
      throw new Error("secret-token-must-not-be-recorded");
    })).rejects.toThrow("secret-token-must-not-be-recorded");

    expect(log.events.every(
      (event) => event.eventType === RuntimeSessionEventType.COMPONENT_LIFECYCLE,
    )).toBe(true);
    expect(log.events.at(-1)?.payload).toEqual({
      componentId: "extension:safe-id",
      previousState: "unloading",
      state: "failed",
      operation: "activate",
      outcome: "failed",
    });
    expect(JSON.stringify(log.toJSON())).not.toContain("secret-token-must-not-be-recorded");
  });
});
