export type LevyScoutIntensity = "local" | "scout" | "jump";

export interface LevyScoutOptions {
  enabled?: boolean;
  seedBase: number;
  generation: number;
  attempt?: number;
  alpha?: number;
  scale?: number;
}

export interface LevyScoutMutation {
  enabled: boolean;
  randomValue: number;
  stepSize: number;
  intensity: LevyScoutIntensity;
  alpha: number;
  scale: number;
}

export function deterministicLevyScoutRandomValue(
  seedBase: number,
  generation: number,
  attempt = 0,
): number {
  let hash = 0x811c9dc5;
  const key = `levy:${seedBase}:${generation}:${attempt}`;
  for (let index = 0; index < key.length; index += 1) {
    hash = Math.imul(hash ^ key.charCodeAt(index), 0x01000193) >>> 0;
  }
  return hash / 0x100000000;
}

export function levyScoutStepSize(randomValue: number, alpha = 1.5, scale = 0.2): number {
  const safeAlpha = Math.max(alpha, 1e-9);
  const safeScale = Math.max(scale, 0);
  const clamped = Math.min(Math.max(randomValue, 1e-12), 1 - 1e-12);
  return Math.min(1, safeScale / ((1 - clamped) ** (1 / safeAlpha)));
}

export function levyScoutIntensity(stepSize: number): LevyScoutIntensity {
  if (stepSize < 0.33) return "local";
  if (stepSize < 0.66) return "scout";
  return "jump";
}

export function evaluateLevyScout(opts: LevyScoutOptions): LevyScoutMutation {
  const alpha = opts.alpha ?? 1.5;
  const scale = opts.scale ?? 0.2;
  const randomValue = deterministicLevyScoutRandomValue(
    opts.seedBase,
    opts.generation,
    opts.attempt ?? 0,
  );
  const stepSize = levyScoutStepSize(randomValue, alpha, scale);
  return {
    enabled: Boolean(opts.enabled),
    randomValue,
    stepSize,
    intensity: levyScoutIntensity(stepSize),
    alpha,
    scale,
  };
}

export function renderLevyScoutGuidance(opts: LevyScoutOptions): string {
  if (!opts.enabled) return "";
  const outcome = evaluateLevyScout(opts);
  const verb = {
    local: "adjust one or two parameters while preserving the current approach",
    scout: "try a noticeably different mix of tactics without ignoring proven constraints",
    jump: "make a broad scout jump and rethink the strategy shape",
  }[outcome.intensity];
  return [
    "Lévy scout mutation guidance:",
    `- intensity: ${outcome.intensity}`,
    `- step_size: ${outcome.stepSize.toFixed(3)}`,
    `- instruction: ${verb}.`,
  ].join("\n");
}
