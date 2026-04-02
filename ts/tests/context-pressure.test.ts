import { describe, expect, it } from "vitest";
import {
  CompactionCircuitBreaker,
  CompactionPolicy,
  CompactionResult,
  ContextPressure,
  PressureLevel,
  effectiveWindow,
} from "../src/session/context-pressure.js";

describe("ContextPressure", () => {
  it("healthy at low utilization", () => {
    const p = ContextPressure.measure(10_000, 100_000);
    expect(p.level).toBe(PressureLevel.HEALTHY);
    expect(p.shouldCompact).toBe(false);
  });

  it("warning at 75%", () => {
    const p = ContextPressure.measure(75_000, 100_000);
    expect(p.level).toBe(PressureLevel.WARNING);
  });

  it("compact_soon at 88%", () => {
    const p = ContextPressure.measure(88_000, 100_000);
    expect(p.level).toBe(PressureLevel.COMPACT_SOON);
    expect(p.shouldCompact).toBe(true);
  });

  it("blocking at 97%", () => {
    const p = ContextPressure.measure(97_000, 100_000);
    expect(p.level).toBe(PressureLevel.BLOCKING);
    expect(p.shouldCompact).toBe(true);
  });

  it("custom thresholds", () => {
    const policy = new CompactionPolicy({ warningThreshold: 0.5, compactThreshold: 0.7, blockingThreshold: 0.9 });
    const p = ContextPressure.measure(60_000, 100_000, policy);
    expect(p.level).toBe(PressureLevel.WARNING);
  });

  it("rejects invalid threshold ordering", () => {
    expect(
      () => new CompactionPolicy({ warningThreshold: 0.9, compactThreshold: 0.7, blockingThreshold: 0.8 }),
    ).toThrow("warningThreshold < compactThreshold < blockingThreshold");
  });

  it("keeps utilization consistent with the chosen level near thresholds", () => {
    const p = ContextPressure.measure(84_996, 100_000);
    expect(p.level).toBe(PressureLevel.WARNING);
    expect(p.utilization).toBeCloseTo(0.84996, 8);
    expect(p.utilization).toBeLessThan(0.85);
  });
});

describe("effectiveWindow", () => {
  it("reserves headroom", () => {
    expect(effectiveWindow(128_000, 4_096, 1_000)).toBe(128_000 - 4_096 - 1_000);
  });

  it("minimum floor > 0", () => {
    expect(effectiveWindow(1_000, 900, 200)).toBeGreaterThan(0);
  });
});

describe("CompactionResult", () => {
  it("tracks tokens freed", () => {
    const r = new CompactionResult({ stage: "micro", tokensBefore: 80_000, tokensAfter: 60_000, safeToContinue: true });
    expect(r.tokensFreed).toBe(20_000);
  });
});

describe("CompactionCircuitBreaker", () => {
  it("trips after max failures", () => {
    const b = new CompactionCircuitBreaker(3);
    expect(b.isOpen).toBe(false);
    b.recordFailure("s1");
    b.recordFailure("s2");
    expect(b.isOpen).toBe(false);
    b.recordFailure("s3");
    expect(b.isOpen).toBe(true);
  });

  it("resets on success", () => {
    const b = new CompactionCircuitBreaker(2);
    b.recordFailure("s1");
    b.recordSuccess();
    b.recordFailure("s2");
    expect(b.isOpen).toBe(false);
  });
});
