export function recordContributionDelta(
  contributions: Map<string, number[]>,
  component: string,
  scoreDelta: number,
): void {
  const existing = contributions.get(component) ?? [];
  existing.push(scoreDelta);
  contributions.set(component, existing);
}

export function recordAttributedCredits(
  contributions: Map<string, number[]>,
  credits: Record<string, number>,
): void {
  for (const [component, credit] of Object.entries(credits)) {
    recordContributionDelta(contributions, component, credit);
  }
}

export function summarizeContributionCredits(
  contributions: Map<string, number[]>,
): Record<string, number> {
  const credits: Record<string, number> = {};
  for (const [component, deltas] of contributions) {
    credits[component] = deltas.reduce((sum, delta) => sum + delta, 0);
  }
  return credits;
}
