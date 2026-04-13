const PRECONDITION_FAMILIES = new Set([
  "simulation",
  "workflow",
  "operator_loop",
  "coordination",
  "investigation",
  "schema_evolution",
  "tool_fragility",
  "negotiation",
]);

export function needsPreconditionHealing(family: string): boolean {
  return PRECONDITION_FAMILIES.has(family);
}

export function normalizePreconditionToken(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

export function healSimulationPreconditions(
  spec: Record<string, unknown>,
): Record<string, unknown> {
  const actions = spec.actions;
  if (!Array.isArray(actions) || actions.length === 0) return spec;

  const actionNames = new Set(
    actions
      .map((a: Record<string, unknown>) => String(a.name ?? ""))
      .filter(Boolean),
  );

  const normalizedMap = new Map<string, string>();
  for (const name of actionNames) {
    normalizedMap.set(normalizePreconditionToken(name), name);
  }

  const healedActions = actions.map((action: Record<string, unknown>) => {
    const preconds = action.preconditions;
    if (!Array.isArray(preconds) || preconds.length === 0) return action;

    const healed = preconds
      .map((p: unknown) => {
        const precondition = String(p);
        if (actionNames.has(precondition)) return precondition;

        const normalized = normalizePreconditionToken(precondition);
        const match = normalizedMap.get(normalized);
        if (match) return match;

        return null;
      })
      .filter((p): p is string => p !== null);

    return { ...action, preconditions: healed };
  });

  return { ...spec, actions: healedActions };
}
