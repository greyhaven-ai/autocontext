/**
 * Tests for AC-342 Task 4: Loop Controller — pause/resume state machine.
 */

import { describe, it, expect } from "vitest";

describe("LoopController", () => {
  it("should be importable", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    expect(LoopController).toBeDefined();
  });

  it("should start in running (not paused) state", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();
    expect(ctrl.isPaused()).toBe(false);
  });

  it("should pause", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();
    ctrl.pause();
    expect(ctrl.isPaused()).toBe(true);
  });

  it("should resume after pause", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();
    ctrl.pause();
    expect(ctrl.isPaused()).toBe(true);
    ctrl.resume();
    expect(ctrl.isPaused()).toBe(false);
  });

  it("waitIfPaused should resolve immediately when not paused", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();
    // Should not hang
    await ctrl.waitIfPaused();
  });

  it("waitIfPaused should block until resumed", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();
    ctrl.pause();

    let resolved = false;
    const promise = ctrl.waitIfPaused().then(() => { resolved = true; });

    // Give microtask a chance to run
    await new Promise(r => setTimeout(r, 20));
    expect(resolved).toBe(false);

    ctrl.resume();
    await promise;
    expect(resolved).toBe(true);
  });

  it("should handle multiple waitIfPaused calls", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();
    ctrl.pause();

    let count = 0;
    const p1 = ctrl.waitIfPaused().then(() => { count++; });
    const p2 = ctrl.waitIfPaused().then(() => { count++; });

    await new Promise(r => setTimeout(r, 20));
    expect(count).toBe(0);

    ctrl.resume();
    await Promise.all([p1, p2]);
    expect(count).toBe(2);
  });
});

describe("LoopController gate override", () => {
  it("should return null when no override set", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();
    expect(ctrl.takeGateOverride()).toBeNull();
  });

  it("should set and take gate override (one-shot)", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();
    ctrl.setGateOverride("advance");
    expect(ctrl.takeGateOverride()).toBe("advance");
    // Second take should return null (consumed)
    expect(ctrl.takeGateOverride()).toBeNull();
  });

  it("should overwrite previous override", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();
    ctrl.setGateOverride("retry");
    ctrl.setGateOverride("rollback");
    expect(ctrl.takeGateOverride()).toBe("rollback");
  });
});

describe("LoopController hint injection", () => {
  it("should return null when no hint", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();
    expect(ctrl.takeHint()).toBeNull();
  });

  it("should inject and take hint (one-shot)", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();
    ctrl.injectHint("Try a defensive strategy");
    expect(ctrl.takeHint()).toBe("Try a defensive strategy");
    expect(ctrl.takeHint()).toBeNull();
  });
});

describe("LoopController chat", () => {
  it("pollChat should return null when no pending chat", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();
    expect(ctrl.pollChat()).toBeNull();
  });

  it("submitChat should enqueue and pollChat should dequeue", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();

    // Submit fires a promise that waits for response
    const responsePromise = ctrl.submitChat("user", "How is the run going?");

    // The loop thread polls
    const msg = ctrl.pollChat();
    expect(msg).not.toBeNull();
    expect(msg![0]).toBe("user");
    expect(msg![1]).toBe("How is the run going?");

    // Loop thread responds
    ctrl.respondChat("assistant", "Generation 3 is in progress.");

    const response = await responsePromise;
    expect(response).toBe("Generation 3 is in progress.");
  });

  it("second pollChat returns null after message consumed", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();

    ctrl.submitChat("user", "msg");
    ctrl.pollChat(); // consume
    expect(ctrl.pollChat()).toBeNull();

    // Don't forget to respond to prevent hanging
    ctrl.respondChat("assistant", "ok");
  });

  it("should preserve FIFO chat responses across multiple polled requests", async () => {
    const { LoopController } = await import("../src/loop/controller.js");
    const ctrl = new LoopController();

    const firstResponse = ctrl.submitChat("user", "first");
    const secondResponse = ctrl.submitChat("user", "second");

    expect(ctrl.pollChat()).toEqual(["user", "first"]);
    expect(ctrl.pollChat()).toEqual(["user", "second"]);

    ctrl.respondChat("assistant", "response-one");
    ctrl.respondChat("assistant", "response-two");

    await expect(firstResponse).resolves.toBe("response-one");
    await expect(secondResponse).resolves.toBe("response-two");
  });
});
