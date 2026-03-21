/**
 * Tests for AC-349: Advanced Features — Curator, Stagnation, Notifications,
 * Dead-End Tracking, Session Reports, Cross-Run Inheritance.
 */

import { describe, it, expect } from "vitest";

// ---------------------------------------------------------------------------
// Task 32: Curator Parsing
// ---------------------------------------------------------------------------

describe("Curator parsing", () => {
  it("parseCuratorPlaybookDecision extracts accept decision", async () => {
    const { parseCuratorPlaybookDecision } = await import("../src/agents/curator-parser.js");
    const text = [
      "Review notes here.",
      "<!-- CURATOR_DECISION: accept -->",
      "<!-- CURATOR_SCORE: 8 -->",
      "<!-- CURATOR_PLAYBOOK_START -->",
      "Updated playbook content.",
      "<!-- CURATOR_PLAYBOOK_END -->",
    ].join("\n");
    const result = parseCuratorPlaybookDecision(text);
    expect(result.decision).toBe("accept");
    expect(result.score).toBe(8);
    expect(result.playbook).toContain("Updated playbook content");
  });

  it("parseCuratorPlaybookDecision handles reject", async () => {
    const { parseCuratorPlaybookDecision } = await import("../src/agents/curator-parser.js");
    const text = "<!-- CURATOR_DECISION: reject -->\n<!-- CURATOR_SCORE: 3 -->";
    const result = parseCuratorPlaybookDecision(text);
    expect(result.decision).toBe("reject");
    expect(result.score).toBe(3);
    expect(result.playbook).toBe("");
  });

  it("parseCuratorPlaybookDecision handles merge", async () => {
    const { parseCuratorPlaybookDecision } = await import("../src/agents/curator-parser.js");
    const text = "<!-- CURATOR_DECISION: merge -->\n<!-- CURATOR_SCORE: 6 -->\n<!-- CURATOR_PLAYBOOK_START -->\nMerged.\n<!-- CURATOR_PLAYBOOK_END -->";
    const result = parseCuratorPlaybookDecision(text);
    expect(result.decision).toBe("merge");
    expect(result.playbook).toContain("Merged");
  });

  it("parseCuratorLessonResult extracts lessons", async () => {
    const { parseCuratorLessonResult } = await import("../src/agents/curator-parser.js");
    const text = [
      "<!-- CONSOLIDATED_LESSONS_START -->",
      "- Lesson A",
      "- Lesson B",
      "<!-- CONSOLIDATED_LESSONS_END -->",
      "<!-- LESSONS_REMOVED: 3 -->",
    ].join("\n");
    const result = parseCuratorLessonResult(text);
    expect(result.consolidatedLessons).toContain("Lesson A");
    expect(result.removedCount).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// Task 36: Stagnation Detection
// ---------------------------------------------------------------------------

describe("StagnationDetector", () => {
  it("detects no stagnation with healthy history", async () => {
    const { StagnationDetector } = await import("../src/loop/stagnation.js");
    const detector = new StagnationDetector();
    const report = detector.detect(
      ["advance", "retry", "advance", "advance"],
      [0.5, 0.55, 0.6, 0.65],
    );
    expect(report.isStagnated).toBe(false);
    expect(report.trigger).toBe("none");
  });

  it("detects consecutive rollbacks", async () => {
    const { StagnationDetector } = await import("../src/loop/stagnation.js");
    const detector = new StagnationDetector({ rollbackThreshold: 3 });
    const report = detector.detect(
      ["advance", "rollback", "rollback", "rollback"],
      [0.5, 0.48, 0.47, 0.46],
    );
    expect(report.isStagnated).toBe(true);
    expect(report.trigger).toBe("consecutive_rollbacks");
  });

  it("detects score plateau", async () => {
    const { StagnationDetector } = await import("../src/loop/stagnation.js");
    const detector = new StagnationDetector({ plateauWindow: 3, plateauEpsilon: 0.01 });
    const report = detector.detect(
      ["advance", "advance", "advance", "advance"],
      [0.50, 0.501, 0.502, 0.501],
    );
    expect(report.isStagnated).toBe(true);
    expect(report.trigger).toBe("score_plateau");
  });

  it("does not flag oscillating scores when stddev exceeds epsilon", async () => {
    const { StagnationDetector } = await import("../src/loop/stagnation.js");
    const detector = new StagnationDetector({ plateauWindow: 4, plateauEpsilon: 0.01 });
    const report = detector.detect(
      ["advance", "advance", "advance", "advance"],
      [0.50, 0.60, 0.50, 0.60],
    );
    expect(report.isStagnated).toBe(false);
    expect(report.trigger).toBe("none");
  });

  it("returns no stagnation when insufficient history", async () => {
    const { StagnationDetector } = await import("../src/loop/stagnation.js");
    const detector = new StagnationDetector({ plateauWindow: 5 });
    const report = detector.detect(["advance"], [0.5]);
    expect(report.isStagnated).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Task 37: Notifications
// ---------------------------------------------------------------------------

describe("Notifications", () => {
  it("StdoutNotifier logs event", async () => {
    const { StdoutNotifier } = await import("../src/notifications/index.js");
    const logged: string[] = [];
    const notifier = new StdoutNotifier((msg: string) => logged.push(msg));
    await notifier.notify({
      type: "completion",
      taskName: "test-task",
      taskId: "t-1",
      score: 0.85,
    });
    expect(logged.length).toBe(1);
    expect(logged[0]).toContain("completion");
  });

  it("CompositeNotifier fans out to multiple notifiers", async () => {
    const { StdoutNotifier, CompositeNotifier } = await import("../src/notifications/index.js");
    const log1: string[] = [];
    const log2: string[] = [];
    const n1 = new StdoutNotifier((msg) => log1.push(msg));
    const n2 = new StdoutNotifier((msg) => log2.push(msg));
    const composite = new CompositeNotifier([n1, n2]);
    await composite.notify({ type: "completion", taskName: "t", taskId: "1", score: 0.9 });
    expect(log1.length).toBe(1);
    expect(log2.length).toBe(1);
  });

  it("CompositeNotifier swallows notifier errors", async () => {
    const { CompositeNotifier } = await import("../src/notifications/index.js");
    const failing = { notify: async () => { throw new Error("boom"); } };
    const log: string[] = [];
    const ok = { notify: async () => { log.push("ok"); } };
    const composite = new CompositeNotifier([failing, ok]);
    await composite.notify({ type: "failure", taskName: "t", taskId: "1", score: 0 });
    expect(log).toEqual(["ok"]);
  });

  it("CallbackNotifier calls provided function", async () => {
    const { CallbackNotifier } = await import("../src/notifications/index.js");
    let received: unknown = null;
    const notifier = new CallbackNotifier((event) => { received = event; });
    await notifier.notify({ type: "threshold_met", taskName: "t", taskId: "1", score: 0.95 });
    expect(received).not.toBeNull();
    expect((received as { type: string }).type).toBe("threshold_met");
  });
});

// ---------------------------------------------------------------------------
// Task 38: Dead-End Tracking
// ---------------------------------------------------------------------------

describe("Dead-End Tracking", () => {
  it("DeadEndEntry.toMarkdown formats correctly", async () => {
    const { DeadEndEntry } = await import("../src/knowledge/dead-end.js");
    const entry = new DeadEndEntry(3, '{"aggression": 0.9}', 0.35, "Score regression");
    const md = entry.toMarkdown();
    expect(md).toContain("Gen 3");
    expect(md).toContain("0.3500");
    expect(md).toContain("Score regression");
  });

  it("DeadEndEntry.fromRollback truncates long strategy", async () => {
    const { DeadEndEntry } = await import("../src/knowledge/dead-end.js");
    const longStrategy = "a".repeat(200);
    const entry = DeadEndEntry.fromRollback(5, longStrategy, 0.40);
    expect(entry.strategySummary.length).toBeLessThanOrEqual(84); // 80 + "..."
    expect(entry.strategySummary).toContain("...");
  });

  it("consolidateDeadEnds trims to max entries", async () => {
    const { consolidateDeadEnds, DeadEndEntry } = await import("../src/knowledge/dead-end.js");
    const entries = Array.from({ length: 10 }, (_, i) =>
      new DeadEndEntry(i + 1, `strategy ${i}`, 0.3, "rollback").toMarkdown(),
    );
    const md = "# Dead-End Registry\n\n" + entries.join("\n") + "\n";
    const result = consolidateDeadEnds(md, 5);
    const kept = result.split("\n").filter((l) => l.startsWith("- **Gen"));
    expect(kept.length).toBe(5);
    // Should keep most recent (6-10)
    expect(kept[0]).toContain("Gen 6");
  });
});

// ---------------------------------------------------------------------------
// Task 39: Session Reports
// ---------------------------------------------------------------------------

describe("Session Reports", () => {
  it("generates report from trajectory data", async () => {
    const { generateSessionReport } = await import("../src/knowledge/session-report.js");
    const rows = [
      { generation_index: 1, best_score: 0.55, elo: 1000, gate_decision: "retry", delta: 0.55, mean_score: 0.50, scoring_backend: "elo" },
      { generation_index: 2, best_score: 0.70, elo: 1050, gate_decision: "advance", delta: 0.15, mean_score: 0.65, scoring_backend: "elo" },
      { generation_index: 3, best_score: 0.85, elo: 1100, gate_decision: "advance", delta: 0.15, mean_score: 0.80, scoring_backend: "elo" },
    ];
    const report = generateSessionReport("run-1", "grid_ctf", rows, { durationSeconds: 125 });
    expect(report.runId).toBe("run-1");
    expect(report.startScore).toBeCloseTo(0.55);
    expect(report.endScore).toBeCloseTo(0.85);
    expect(report.totalGenerations).toBe(3);
    expect(report.gateCounts.advance).toBe(2);
    expect(report.gateCounts.retry).toBe(1);
    expect(report.topImprovements.length).toBeGreaterThan(0);
  });

  it("renders markdown report", async () => {
    const { generateSessionReport } = await import("../src/knowledge/session-report.js");
    const rows = [
      { generation_index: 1, best_score: 0.55, elo: 1000, gate_decision: "advance", delta: 0.55, mean_score: 0.50, scoring_backend: "elo" },
    ];
    const report = generateSessionReport("run-1", "grid_ctf", rows);
    const md = report.toMarkdown();
    expect(md).toContain("# Session Report");
    expect(md).toContain("run-1");
    expect(md).toContain("grid_ctf");
    expect(md).toContain("0.5500");
  });

  it("handles empty trajectory", async () => {
    const { generateSessionReport } = await import("../src/knowledge/session-report.js");
    const report = generateSessionReport("run-1", "grid_ctf", []);
    expect(report.totalGenerations).toBe(0);
    expect(report.startScore).toBe(0);
    expect(report.endScore).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Task 35: Cross-Run Inheritance (knowledge snapshot)
// ---------------------------------------------------------------------------

describe("Cross-Run Inheritance", () => {
  it("SkillPackage.toDict serializes cross-run knowledge fields", async () => {
    const { SkillPackage } = await import("../src/knowledge/skill-package.js");
    const pkg = new SkillPackage({
      scenarioName: "grid_ctf",
      displayName: "Grid CTF",
      description: "Capture the flag game",
      playbook: "# Playbook",
      lessons: ["lesson 1"],
      bestStrategy: { aggression: 0.6 },
      bestScore: 0.85,
      bestElo: 1100,
      hints: "",
    });
    const data = pkg.toDict();
    expect(data.scenario_name).toBe("grid_ctf");
    expect(data.best_score).toBe(0.85);
    expect(data.best_elo).toBe(1100);
    expect(data.playbook).toBe("# Playbook");
    expect((data.lessons as string[])[0]).toBe("lesson 1");
  });
});
