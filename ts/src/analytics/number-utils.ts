/**
 * Shared analytics numeric helpers.
 */

export function roundToDecimals(value: number, digits = 6): number {
  return Number(value.toFixed(digits));
}
