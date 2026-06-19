import { describe, expect, it } from "vitest";
import {
  buildTokenPressureReport,
  compareTokenPressureReports,
  type TokenPressureObservation,
} from "../src/training/token-pressure-diagnostics.js";

describe("token pressure diagnostics", () => {
  it("summarizes teacher/student pressure without raw token text by default", () => {
    const observations: TokenPressureObservation[] = [
      {
        position: 0,
        studentLogprob: -2,
        teacherLogprob: -1,
        studentEntropy: 0.7,
        tokenText: "secret",
      },
      { position: 1, studentLogprob: -0.1, teacherLogprob: -1.1, studentEntropy: 0.2 },
      {
        position: 1,
        studentLogprob: -4,
        teacherLogprob: -0.5,
        studentEntropy: 0.3,
        tokenText: "spike",
      },
    ];

    const report = buildTokenPressureReport(observations, {
      backend: "trl",
      mode: "gkd",
      seed: 7,
      responseLengths: [2, 3],
      shockThreshold: 2,
    });

    expect(report.tokenCount).toBe(3);
    expect(report.positivePressureRatio).toBe(2 / 3);
    expect(report.negativePressureRatio).toBe(1 / 3);
    expect(report.meanPositiveMargin).toBe(2.25);
    expect(report.meanNegativeMargin).toBe(-1);
    expect(report.meanResponseLength).toBe(2.5);
    expect(report.shockSpikeCount).toBe(1);
    expect(report.positionPressure[1].count).toBe(2);
    expect(report.rawTokenTextPersisted).toBe(false);
    expect(JSON.stringify(report)).not.toContain("secret");
  });

  it("only persists debug token text by explicit opt-in", () => {
    const report = buildTokenPressureReport(
      [{ position: 0, studentLogprob: -5, teacherLogprob: -1, tokenText: "debug-token" }],
      { backend: "opd", mode: "opd", includeTokenText: true, shockThreshold: 1 },
    );

    expect(report.rawTokenTextPersisted).toBe(true);
    expect(report.shockSpikes[0].tokenText).toBe("debug-token");
  });

  it("compares diagnostic runs for A/B pressure checks", () => {
    const comparison = compareTokenPressureReports([
      {
        runId: "neg",
        positivePressureRatio: 0.25,
        negativePressureRatio: 0.75,
        shockSpikeCount: 3,
      },
      {
        runId: "pos",
        positivePressureRatio: 0.75,
        negativePressureRatio: 0.25,
        shockSpikeCount: 1,
      },
    ]);

    expect(comparison.runCount).toBe(2);
    expect(comparison.meanPositivePressureRatio).toBe(0.5);
    expect(comparison.highestPositivePressureRunId).toBe("pos");
    expect(comparison.highestShockRunId).toBe("neg");
  });
});
