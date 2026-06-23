import type { GateDecision } from "./backpressure.js";

export interface AnnealingOptions {
  enabled?: boolean;
  generation: number;
  randomValue: number;
  startTemperature?: number;
  endTemperature?: number;
  generations?: number;
}

export interface AnnealingOutcome {
  accepted: boolean;
  temperature: number;
  acceptanceProbability: number;
  randomValue: number;
  delta: number;
}

export function annealingTemperature(opts: AnnealingOptions): number {
  const start = opts.startTemperature ?? 0.05;
  const end = opts.endTemperature ?? 0.001;
  const generations = opts.generations ?? 20;
  const span = Math.max(1, generations - 1);
  const progress = Math.min(1, Math.max(0, (opts.generation - 1) / span));
  return Math.max(0, start + (end - start) * progress);
}

export function deterministicAnnealingRandomValue(
  seedBase: number,
  generation: number,
  attempt: number,
): number {
  let hash = 0x811c9dc5;
  const key = `${seedBase}:${generation}:${attempt}`;
  for (let index = 0; index < key.length; index += 1) {
    hash = Math.imul(hash ^ key.charCodeAt(index), 0x01000193) >>> 0;
  }
  return hash / 0x100000000;
}

export function evaluateAnnealing(delta: number, opts: AnnealingOptions): AnnealingOutcome {
  const temperature = annealingTemperature(opts);
  const probability = opts.enabled && delta < 0 && temperature > 0
    ? Math.min(1, Math.max(0, Math.exp(delta / temperature)))
    : 0;
  return {
    accepted: Boolean(opts.enabled && delta < 0 && opts.randomValue < probability),
    temperature,
    acceptanceProbability: probability,
    randomValue: opts.randomValue,
    delta,
  };
}

export function applyAnnealingToGateDecision(
  decision: GateDecision,
  opts: AnnealingOptions,
): GateDecision {
  if (!opts.enabled) return decision;

  const outcome = evaluateAnnealing(decision.delta, opts);
  const metadata = {
    ...decision.metadata,
    annealing: {
      accepted: outcome.accepted,
      temperature: outcome.temperature,
      acceptance_probability: outcome.acceptanceProbability,
      random_value: outcome.randomValue,
      delta: outcome.delta,
    },
  };

  if (decision.decision === "advance" || !outcome.accepted) return { ...decision, metadata };
  return {
    ...decision,
    decision: "advance",
    reason: `${decision.reason}; annealing accepted regression`,
    metadata,
  };
}
