import { join } from "node:path";

import { assertSafeScenarioId } from "../knowledge/scenario-id.js";
import type { ScenarioFamilyName } from "./families.js";

export interface MaterializeRequestPlanningResult {
  family: ScenarioFamilyName;
  healedSpec: Record<string, unknown>;
  scenarioDir: string;
  scenarioType: string;
}

export function planMaterializeScenarioRequest(opts: {
  family: string;
  name: string;
  spec: Record<string, unknown>;
  knowledgeRoot: string;
  coerceMaterializeFamily: (family: string) => ScenarioFamilyName;
  healSpec: (spec: Record<string, unknown>, family: string) => Record<string, unknown>;
  getScenarioTypeMarker: (family: ScenarioFamilyName) => string;
}): MaterializeRequestPlanningResult {
  const safeName = assertSafeScenarioId(opts.name, "scenario name");
  const family = opts.coerceMaterializeFamily(opts.family);
  const healedSpec = opts.healSpec(opts.spec, family);

  return {
    family,
    healedSpec,
    scenarioDir: join(opts.knowledgeRoot, "_custom_scenarios", safeName),
    scenarioType: opts.getScenarioTypeMarker(family),
  };
}
