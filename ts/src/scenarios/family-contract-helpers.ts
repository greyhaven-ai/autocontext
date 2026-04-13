export type MethodVariant = string | readonly string[];

const SIMULATION_METHOD_VARIANTS: MethodVariant[] = [
  ["describeScenario", "describe_scenario"],
  ["describeEnvironment", "describe_environment"],
  ["initialState", "initial_state"],
  ["getAvailableActions", "get_available_actions"],
  ["executeAction", "execute_action"],
  ["isTerminal", "is_terminal"],
  ["evaluateTrace", "evaluate_trace"],
  ["getRubric", "get_rubric"],
];

export function hasMethodVariants(
  obj: unknown,
  ...variants: MethodVariant[]
): boolean {
  if (!obj || typeof obj !== "object") return false;
  const candidate = obj as Record<string, unknown>;
  return variants.every((variant) => {
    const names = Array.isArray(variant) ? variant : [variant];
    return names.some((name) => typeof candidate[name] === "function");
  });
}

export function hasSimulationMethodVariants(
  obj: unknown,
  ...variants: MethodVariant[]
): boolean {
  return hasMethodVariants(obj, ...SIMULATION_METHOD_VARIANTS, ...variants);
}

export function formatExpectedMethods(methods: readonly string[]): string {
  return methods.join(", ");
}
