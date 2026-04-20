// RED-phase TDD: this file references files that will exist once AC-545 lands.
// The first green cycle is "actuator registers on import with the expected
// rollback strategy and target pattern".

import { describe, test, expect } from "vitest";
import {
  __resetActuatorRegistryForTests,
  getActuator,
  registerActuator,
  type ActuatorRegistration,
} from "../../../../src/control-plane/actuators/registry.js";
import { modelRoutingRegistration } from "../../../../src/control-plane/actuators/model-routing/index.js";

describe("model-routing actuator registration", () => {
  test("declares content-revert rollback and a routing/models/*.json target pattern", () => {
    expect(modelRoutingRegistration.type).toBe("model-routing");
    expect(modelRoutingRegistration.rollback).toEqual({ kind: "content-revert" });
    expect(modelRoutingRegistration.allowedTargetPattern).toMatch(/routing\/models/);
    expect(modelRoutingRegistration.allowedTargetPattern).toMatch(/\.json$/);
  });

  test("is discoverable via getActuator after importing the module", () => {
    expect(getActuator("model-routing")).not.toBeNull();
  });

  test("registry refuses a wrong-rollback registration for model-routing", () => {
    __resetActuatorRegistryForTests();
    const wrong: ActuatorRegistration<unknown> = {
      type: "model-routing",
      rollback: { kind: "pointer-flip" },
      allowedTargetPattern: "**/routing/models/*.json",
      actuator: modelRoutingRegistration.actuator,
    };
    expect(() => registerActuator(wrong)).toThrow(/content-revert/i);
  });

  test("registry refuses an empty allowedTargetPattern for model-routing", () => {
    __resetActuatorRegistryForTests();
    const wrong: ActuatorRegistration<unknown> = {
      type: "model-routing",
      rollback: { kind: "content-revert" },
      allowedTargetPattern: "",
      actuator: modelRoutingRegistration.actuator,
    };
    expect(() => registerActuator(wrong)).toThrow(/allowedTargetPattern/i);
  });
});
