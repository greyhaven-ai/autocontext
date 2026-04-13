import { formatExpectedMethods } from "./family-contract-helpers.js";
import type { ScenarioFamilyName } from "./families.js";

export type FamilyGuard = (obj: unknown) => boolean;
export type OrderedFamilyDetector = readonly [ScenarioFamilyName, FamilyGuard];

export function assertFamilyContractWithCatalog(opts: {
  obj: unknown;
  family: ScenarioFamilyName;
  context?: string;
  guards: Record<ScenarioFamilyName, FamilyGuard>;
  expectedMethods: Record<ScenarioFamilyName, readonly string[]>;
}): void {
  if (opts.guards[opts.family](opts.obj)) {
    return;
  }
  throw new Error(
    `${opts.context ?? "runtime object"} does not satisfy '${opts.family}' contract. Expected methods: ${formatExpectedMethods(opts.expectedMethods[opts.family])}`,
  );
}

export function detectFamilyWithDetectors(
  obj: unknown,
  orderedDetectors: readonly OrderedFamilyDetector[],
): ScenarioFamilyName | null {
  for (const [family, guard] of orderedDetectors) {
    if (guard(obj)) {
      return family;
    }
  }
  return null;
}
