/**
 * Shared analytics numeric helpers.
 */

export function roundToDecimals(value: number, digits = 6): number {
  return Number(value.toFixed(digits));
}

export function normalizeConfidence(value: number): number {
  if (!Number.isFinite(value)) {
    return 0;
  }
  return roundToDecimals(Math.min(1, Math.max(0, value)), 4);
}

export function normalizePreviewThreshold(value: number): number {
  return normalizeConfidence(value);
}
