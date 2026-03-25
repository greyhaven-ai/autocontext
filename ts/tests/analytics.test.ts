/**
 * Tests for AC-381: Analytics module — run traces, rubric drift, credit assignment.
 */

import { describe, it, expect } from "vitest";

// ---------------------------------------------------------------------------
// Run trace types
// ---------------------------------------------------------------------------

describe("RunTrace types", () => {
  it("exports ActorRef, TraceEvent, RunTrace", async () => {
    const mod = await import("../src/analytics/run-trace.js");
    expect(mod.ActorRef).toBeDefined();
    expect(mod.TraceEvent).toBeDefined();
    expect(mod.RunTrace).toBeDefined();
  });

  it("ActorRef serializes to/from dict", async () => {
    const { ActorRef } = await import("../src/analytics/run-trace.js");
    const actor = new ActorRef("role", "competitor", "Competitor Agent");
    const dict = actor.toDict();
    expect(dict.actor_type).toBe("role");
    const restored = ActorRef.fromDict(dict);
    expect(restored.actorId).toBe("competitor");
  });

  it("TraceEvent records timestamped events", async () => {
    const { TraceEvent, ActorRef } = await import("../src/analytics/run-trace.js");
    const event = new TraceEvent({
      eventType: "agent_output",
      actor: new ActorRef("role", "analyst", "Analyst"),
      payload: { content: "Analysis complete." },
    });
    expect(event.eventType).toBe("agent_output");
    expect(event.timestamp).toBeDefined();
  });

  it("RunTrace collects events", async () => {
    const { RunTrace, TraceEvent, ActorRef } = await import("../src/analytics/run-trace.js");
    const trace = new RunTrace("run-1", "grid_ctf");
    trace.addEvent(new TraceEvent({
      eventType: "generation_started",
      actor: new ActorRef("system", "loop", "GenerationRunner"),
      payload: { generation: 1 },
    }));
    expect(trace.events.length).toBe(1);
    expect(trace.runId).toBe("run-1");
  });

  it("RunTrace serializes to/from JSON", async () => {
    const { RunTrace, TraceEvent, ActorRef } = await import("../src/analytics/run-trace.js");
    const trace = new RunTrace("run-1", "grid_ctf");
    trace.addEvent(new TraceEvent({
      eventType: "test",
      actor: new ActorRef("system", "test", "Test"),
      payload: {},
    }));
    const json = trace.toJSON();
    const restored = RunTrace.fromJSON(json);
    expect(restored.events.length).toBe(1);
    expect(restored.runId).toBe("run-1");
    expect(restored.createdAt).toBe(trace.createdAt);
  });
});

// ---------------------------------------------------------------------------
// Rubric drift detection
// ---------------------------------------------------------------------------

describe("Rubric drift", () => {
  it("exports RubricDriftMonitor", async () => {
    const { RubricDriftMonitor } = await import("../src/analytics/rubric-drift.js");
    expect(RubricDriftMonitor).toBeDefined();
  });

  it("detects score inflation", async () => {
    const { RubricDriftMonitor } = await import("../src/analytics/rubric-drift.js");
    const monitor = new RubricDriftMonitor();

    const facets = [
      {
        scenario: "grid_ctf",
        bestScore: 0.5,
        createdAt: "2026-01-01T00:00:00Z",
        totalGenerations: 2,
        delightSignals: [],
        retries: 0,
        rollbacks: 0,
      },
      {
        scenario: "grid_ctf",
        bestScore: 0.6,
        createdAt: "2026-01-02T00:00:00Z",
        totalGenerations: 2,
        delightSignals: [],
        retries: 0,
        rollbacks: 0,
      },
      {
        scenario: "grid_ctf",
        bestScore: 0.9,
        createdAt: "2026-01-03T00:00:00Z",
        totalGenerations: 2,
        delightSignals: [{ signalType: "strong_improvement" }],
        retries: 1,
        rollbacks: 0,
      },
      {
        scenario: "grid_ctf",
        bestScore: 0.98,
        createdAt: "2026-01-04T00:00:00Z",
        totalGenerations: 2,
        delightSignals: [{ signalType: "strong_improvement" }],
        retries: 2,
        rollbacks: 1,
      },
    ];

    const report = monitor.analyze(facets, {
      release: "0.2.4",
      scenarioFamily: "game",
      agentProvider: "anthropic",
    });
    expect(report.warnings.length).toBeGreaterThan(0);
    expect(report.snapshot.agentProvider).toBe("anthropic");
    expect(report.warnings.some((warning) => warning.warningType === "score_inflation" || warning.warningType === "perfect_rate_high")).toBe(true);
  });

  it("reports no drift for stable scores", async () => {
    const { RubricDriftMonitor } = await import("../src/analytics/rubric-drift.js");
    const monitor = new RubricDriftMonitor();

    const stableScores = [0.6, 0.7, 0.74, 0.68, 0.78, 0.69, 0.63, 0.75];
    for (const score of stableScores) {
      monitor.recordScore(score);
    }

    const report = monitor.analyze();
    expect(report.stable).toBe(true);
  });

  it("detects baseline inflation separately from within-window inflation", async () => {
    const { RubricDriftMonitor } = await import("../src/analytics/rubric-drift.js");
    const monitor = new RubricDriftMonitor();
    const baseline = monitor.computeSnapshot([
      {
        scenario: "grid_ctf",
        bestScore: 0.5,
        createdAt: "2026-01-01T00:00:00Z",
        totalGenerations: 1,
        delightSignals: [],
        retries: 0,
        rollbacks: 0,
      },
      {
        scenario: "grid_ctf",
        bestScore: 0.55,
        createdAt: "2026-01-02T00:00:00Z",
        totalGenerations: 1,
        delightSignals: [],
        retries: 0,
        rollbacks: 0,
      },
    ]);

    const report = monitor.analyze([
      {
        scenario: "grid_ctf",
        bestScore: 0.8,
        createdAt: "2026-02-01T00:00:00Z",
        totalGenerations: 1,
        delightSignals: [],
        retries: 0,
        rollbacks: 0,
      },
      {
        scenario: "grid_ctf",
        bestScore: 0.85,
        createdAt: "2026-02-02T00:00:00Z",
        totalGenerations: 1,
        delightSignals: [],
        retries: 0,
        rollbacks: 0,
      },
    ], { baseline });

    expect(report.warnings.some((warning) => warning.metricName === "mean_score_delta")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Credit assignment
// ---------------------------------------------------------------------------

describe("Credit assignment", () => {
  it("exports CreditAssigner", async () => {
    const { CreditAssigner } = await import("../src/analytics/credit-assignment.js");
    expect(CreditAssigner).toBeDefined();
  });

  it("assigns credit to components based on score impact", async () => {
    const {
      CreditAssigner,
      attributeCredit,
      computeChangeVector,
      formatAttributionForAgent,
    } = await import("../src/analytics/credit-assignment.js");
    const assigner = new CreditAssigner();

    const vector = computeChangeVector(
      3,
      0.3,
      {
        playbook: "old plan",
        tools: ["grep"],
        hints: "keep it simple",
        analysis: "weak hypothesis",
      },
      {
        playbook: "new plan with branches",
        tools: ["grep", "rg"],
        hints: "focus on invariants",
        analysis: "stronger hypothesis with evidence",
      },
    );

    const attribution = attributeCredit(vector);
    expect(vector.changes.length).toBeGreaterThan(0);
    expect(attribution.totalDelta).toBe(0.3);
    expect(Object.values(attribution.credits).reduce((sum, value) => sum + value, 0)).toBeCloseTo(0.3, 5);

    const formatted = formatAttributionForAgent(attribution, "coach");
    expect(formatted).toContain("Previous Coaching Attribution");
    expect(formatted).toContain("Total score improvement");

    assigner.attributeCredit(vector);
    const credits = assigner.getCredits();
    expect(credits.playbook).toBeGreaterThan(0);
    expect(credits.tools).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Timeline inspector
// ---------------------------------------------------------------------------

describe("Timeline inspector", () => {
  it("exports TimelineInspector", async () => {
    const { TimelineInspector } = await import("../src/analytics/timeline-inspector.js");
    expect(TimelineInspector).toBeDefined();
  });

  it("inspects generation timeline from events", async () => {
    const { TimelineInspector } = await import("../src/analytics/timeline-inspector.js");
    const inspector = new TimelineInspector();

    inspector.addEvent({ type: "generation_started", generation: 1, timestamp: "2026-01-01T00:00:00Z" });
    inspector.addEvent({ type: "tournament_completed", generation: 1, mean_score: 0.65, timestamp: "2026-01-01T00:01:00Z" });
    inspector.addEvent({ type: "gate_decided", generation: 1, decision: "advance", timestamp: "2026-01-01T00:01:05Z" });

    const summary = inspector.summarize();
    expect(summary.generations.length).toBe(1);
    expect(summary.generations[0].gateDecision).toBe("advance");
  });
});
