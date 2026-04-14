export function readMetric(
  metrics: Record<string, number> | undefined,
  ...keys: string[]
): number | undefined {
  if (!metrics) {
    return undefined;
  }

  for (const key of keys) {
    const value = metrics[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
  }

  return undefined;
}
