import type {
  ActionDict,
  HarnessLoaderLike,
  ScenarioLike,
} from "./action-filter-contracts.js";

export function getHarnessActions(
  harnessLoader: HarnessLoaderLike | null,
  state: Record<string, unknown>,
): ActionDict[] | null {
  if (!harnessLoader) {
    return null;
  }
  for (const validator of harnessLoader.validators) {
    if (typeof validator.enumerate_legal_actions !== "function") {
      continue;
    }
    try {
      const result = validator.enumerate_legal_actions(state);
      if (Array.isArray(result)) {
        return result;
      }
    } catch {
      // ignore failing validator and continue to the next one
    }
  }
  return null;
}

export function getLegalActions(
  scenario: ScenarioLike,
  state: Record<string, unknown>,
  harnessLoader: HarnessLoaderLike | null,
): ActionDict[] | null {
  const result = scenario.enumerateLegalActions(state);
  if (result !== null) {
    return result;
  }
  return getHarnessActions(harnessLoader, state);
}
