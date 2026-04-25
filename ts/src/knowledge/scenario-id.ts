const SAFE_SCENARIO_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;

export function isSafeScenarioId(value: string): boolean {
  return SAFE_SCENARIO_ID_RE.test(value);
}

export function assertSafeScenarioId(value: string, fieldName = "scenario"): string {
  if (isSafeScenarioId(value)) {
    return value;
  }
  throw new Error(
    `${fieldName} must be a safe scenario identifier ` +
      "(letters, digits, underscores, or hyphens; no path separators)",
  );
}
