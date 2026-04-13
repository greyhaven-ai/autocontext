import {
  assertFamilyContractWithCatalog,
  detectFamilyWithDetectors,
} from "./family-assertion-workflow.js";
import { EXPECTED_METHODS } from "./family-expected-methods.js";
import {
  FAMILY_INTERFACE_DETECTOR_ORDER,
  FAMILY_INTERFACE_GUARD_CATALOG,
} from "./family-interface-registry.js";
import type { ScenarioFamilyName } from "./family-interface-types.js";

export function assertFamilyContract(
  obj: unknown,
  family: ScenarioFamilyName,
  context = "runtime object",
): void {
  assertFamilyContractWithCatalog({
    obj,
    family,
    context,
    guards: FAMILY_INTERFACE_GUARD_CATALOG,
    expectedMethods: EXPECTED_METHODS,
  });
}

export function detectFamily(obj: unknown): ScenarioFamilyName | null {
  return detectFamilyWithDetectors(obj, FAMILY_INTERFACE_DETECTOR_ORDER);
}
