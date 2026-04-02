/**
 * Adaptive context-pressure management (AC-508 TS parity).
 *
 * Port of Python autocontext.session.context_pressure.
 */

export const PressureLevel = {
  HEALTHY: "healthy",
  WARNING: "warning",
  COMPACT_SOON: "compact_soon",
  BLOCKING: "blocking",
} as const;
export type PressureLevel = (typeof PressureLevel)[keyof typeof PressureLevel];

export class CompactionPolicy {
  readonly warningThreshold: number;
  readonly compactThreshold: number;
  readonly blockingThreshold: number;

  constructor(opts?: { warningThreshold?: number; compactThreshold?: number; blockingThreshold?: number }) {
    this.warningThreshold = opts?.warningThreshold ?? 0.70;
    this.compactThreshold = opts?.compactThreshold ?? 0.85;
    this.blockingThreshold = opts?.blockingThreshold ?? 0.95;
  }
}

export class ContextPressure {
  readonly usedTokens: number;
  readonly effectiveWindow: number;
  readonly utilization: number;
  readonly level: PressureLevel;

  private constructor(usedTokens: number, effectiveWindow: number, utilization: number, level: PressureLevel) {
    this.usedTokens = usedTokens;
    this.effectiveWindow = effectiveWindow;
    this.utilization = utilization;
    this.level = level;
  }

  get shouldCompact(): boolean {
    return this.level === PressureLevel.COMPACT_SOON || this.level === PressureLevel.BLOCKING;
  }

  get tokensRemaining(): number {
    return Math.max(0, this.effectiveWindow - this.usedTokens);
  }

  static measure(usedTokens: number, effectiveWindow: number, policy?: CompactionPolicy): ContextPressure {
    const p = policy ?? new CompactionPolicy();
    const util = usedTokens / Math.max(effectiveWindow, 1);

    let level: PressureLevel;
    if (util >= p.blockingThreshold) level = PressureLevel.BLOCKING;
    else if (util >= p.compactThreshold) level = PressureLevel.COMPACT_SOON;
    else if (util >= p.warningThreshold) level = PressureLevel.WARNING;
    else level = PressureLevel.HEALTHY;

    return new ContextPressure(usedTokens, effectiveWindow, Math.round(util * 10000) / 10000, level);
  }
}

export class CompactionResult {
  readonly stage: string;
  readonly tokensBefore: number;
  readonly tokensAfter: number;
  readonly safeToContinue: boolean;

  constructor(opts: { stage: string; tokensBefore: number; tokensAfter: number; safeToContinue: boolean }) {
    this.stage = opts.stage;
    this.tokensBefore = opts.tokensBefore;
    this.tokensAfter = opts.tokensAfter;
    this.safeToContinue = opts.safeToContinue;
  }

  get tokensFreed(): number {
    return this.tokensBefore - this.tokensAfter;
  }
}

export function effectiveWindow(raw: number, outputHeadroom: number = 4096, overhead: number = 512): number {
  return Math.max(1, raw - outputHeadroom - overhead);
}

export class CompactionCircuitBreaker {
  private maxFailures: number;
  private consecutiveFailures = 0;

  constructor(maxFailures: number = 3) {
    this.maxFailures = maxFailures;
  }

  get isOpen(): boolean {
    return this.consecutiveFailures >= this.maxFailures;
  }

  recordFailure(stage: string): void {
    this.consecutiveFailures++;
  }

  recordSuccess(): void {
    this.consecutiveFailures = 0;
  }
}
