export const SIMULATION_NUMERIC_DECIMALS = 4;

function normalizeSimulationNumber(value: number): number {
  return Number(value.toFixed(SIMULATION_NUMERIC_DECIMALS));
}

export function normalizeSimulationScore(value: number): number {
  return normalizeSimulationNumber(value);
}

export function normalizeSimulationDelta(value: number): number {
  return normalizeSimulationNumber(value);
}

export function normalizeSimulationSweepValue(value: number): number {
  return normalizeSimulationNumber(value);
}
