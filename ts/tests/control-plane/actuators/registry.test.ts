import { describe, test, expect, beforeEach } from "vitest";
import {
  registerActuator,
  getActuator,
  listActuatorTypes,
  __resetActuatorRegistryForTests,
  type ActuatorRegistration,
  type Actuator,
} from "../../../src/control-plane/actuators/registry.js";
import type { ActuatorType, RollbackStrategy } from "../../../src/control-plane/contract/types.js";

// Minimal stand-in actuator — real ones live in the concrete actuator dirs.
function stubActuator<P>(): Actuator<P> {
  return {
    resolveTargetPath: (() => "/tmp/x") as Actuator<P>["resolveTargetPath"],
    apply: (async () => {}) as Actuator<P>["apply"],
    emitPatch: (() => ({
      filePath: "/tmp/x",
      operation: "modify",
      unifiedDiff: "",
    })) as Actuator<P>["emitPatch"],
    rollback: (async () => ({
      filePath: "/tmp/x",
      operation: "modify",
      unifiedDiff: "",
    })) as Actuator<P>["rollback"],
    parsePayload: ((x: unknown) => x) as Actuator<P>["parsePayload"],
  };
}

function reg<P>(type: ActuatorType, rollback: RollbackStrategy): ActuatorRegistration<P> {
  return {
    type,
    rollback,
    allowedTargetPattern: "**/anywhere/**",
    actuator: stubActuator<P>(),
  };
}

describe("actuator registry", () => {
  beforeEach(() => {
    __resetActuatorRegistryForTests();
  });

  test("registerActuator + getActuator round-trip a registration", () => {
    const r = reg("prompt-patch", { kind: "content-revert" });
    registerActuator(r);
    expect(getActuator("prompt-patch")).toBe(r);
  });

  test("getActuator returns null for unregistered types", () => {
    expect(getActuator("tool-policy")).toBeNull();
  });

  test("listActuatorTypes returns every registered type (order-insensitive)", () => {
    registerActuator(reg("prompt-patch", { kind: "content-revert" }));
    registerActuator(reg("tool-policy", { kind: "content-revert" }));
    expect(listActuatorTypes().sort()).toEqual(["prompt-patch", "tool-policy"].sort());
  });

  test("rejects duplicate registration for the same actuator type", () => {
    registerActuator(reg("prompt-patch", { kind: "content-revert" }));
    expect(() =>
      registerActuator(reg("prompt-patch", { kind: "content-revert" })),
    ).toThrow(/already registered/i);
  });

  test("rejects prompt-patch registered with pointer-flip rollback (minimum is content-revert)", () => {
    expect(() =>
      registerActuator(reg("prompt-patch", { kind: "pointer-flip" })),
    ).toThrow(/content-revert/i);
  });

  test("rejects tool-policy registered with pointer-flip rollback (minimum is content-revert)", () => {
    expect(() =>
      registerActuator(reg("tool-policy", { kind: "pointer-flip" })),
    ).toThrow(/content-revert/i);
  });

  test("rejects routing-rule registered without cascade-set rollback", () => {
    expect(() =>
      registerActuator(reg("routing-rule", { kind: "content-revert" })),
    ).toThrow(/cascade-set/i);
  });

  test("accepts routing-rule with cascade-set rollback", () => {
    const r = reg<unknown>("routing-rule", {
      kind: "cascade-set",
      dependsOn: ["tool-policy"],
    });
    registerActuator(r);
    expect(getActuator("routing-rule")).toBe(r);
  });

  test("rejects fine-tuned-model registered with content-revert rollback (minimum is pointer-flip)", () => {
    expect(() =>
      registerActuator(reg("fine-tuned-model", { kind: "content-revert" })),
    ).toThrow(/pointer-flip/i);
  });

  test("accepts fine-tuned-model with pointer-flip rollback", () => {
    const r = reg("fine-tuned-model", { kind: "pointer-flip" });
    registerActuator(r);
    expect(getActuator("fine-tuned-model")).toBe(r);
  });

  test("rejects registration with empty allowedTargetPattern", () => {
    const r: ActuatorRegistration<unknown> = {
      type: "prompt-patch",
      rollback: { kind: "content-revert" },
      allowedTargetPattern: "",
      actuator: stubActuator(),
    };
    expect(() => registerActuator(r)).toThrow(/allowedTargetPattern/i);
  });
});
