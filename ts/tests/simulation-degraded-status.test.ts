import { describe, it, expect } from "vitest";
import {
  deriveSimulationStatus,
  DEGRADED_SCORE_THRESHOLD,
} from "../src/simulation/engine.js";

describe("simulation status derivation (AC-532)", () => {
  it("returns 'completed' for scores >= threshold", () => {
    expect(deriveSimulationStatus(0.5)).toBe("completed");
    expect(deriveSimulationStatus(0.2)).toBe("completed");
    expect(deriveSimulationStatus(1.0)).toBe("completed");
  });

  it("returns 'degraded' for scores < threshold", () => {
    expect(deriveSimulationStatus(0.04)).toBe("degraded");
    expect(deriveSimulationStatus(0.0)).toBe("degraded");
    expect(deriveSimulationStatus(0.19)).toBe("degraded");
  });

  it("returns 'degraded' at the boundary (just below threshold)", () => {
    expect(deriveSimulationStatus(0.1999)).toBe("degraded");
  });

  it("returns 'completed' at exactly the threshold", () => {
    expect(deriveSimulationStatus(DEGRADED_SCORE_THRESHOLD)).toBe("completed");
  });

  it("exports DEGRADED_SCORE_THRESHOLD as 0.2", () => {
    expect(DEGRADED_SCORE_THRESHOLD).toBe(0.2);
  });
});
