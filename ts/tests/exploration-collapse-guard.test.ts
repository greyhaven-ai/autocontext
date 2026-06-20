import { describe, expect, it } from "vitest";

type ExplorationSnapshot = {
  generationIndex: number;
  responseLength: number;
  diversity?: number;
  entropy?: number;
  routeSignature?: string;
  rollbackRate?: number;
  score?: number;
};

type GuidanceChange = {
  changeId: string;
  generationIndex: number;
  kind: "hint" | "playbook_update" | "teacher_signal" | "pressure_mode" | "other";
  sourceComponent: string;
  sourceSpan?: string;
};

type Report = {
  events: Array<{
    guidanceChange: GuidanceChange;
    advisoryOnly: boolean;
    mitigation: string;
    signals: Array<{ metric: string }>;
  }>;
  records: Array<Record<string, unknown>>;
};

type Protocol = {
  detectExplorationCollapse: (
    snapshots: ExplorationSnapshot[],
    changes: GuidanceChange[],
    options?: { advisoryOnly?: boolean; autoMitigation?: boolean },
  ) => Report;
  renderExplorationCollapseReport: (report: Report) => string;
};

async function protocol(): Promise<Protocol> {
  const path = "../src/analytics/exploration-collapse-guard.js";
  return (await import(path)) as Protocol;
}

function snapshot(
  generationIndex: number,
  responseLength: number,
  diversity: number,
  entropy: number,
  routeSignature: string,
  rollbackRate: number,
  score: number,
): ExplorationSnapshot {
  return {
    generationIndex,
    responseLength,
    diversity,
    entropy,
    routeSignature,
    rollbackRate,
    score,
  };
}

function collapsedRun(): { snapshots: ExplorationSnapshot[]; changes: GuidanceChange[] } {
  return {
    snapshots: [
      snapshot(0, 120, 0.82, 3.3, "wide-a", 0.05, 0.61),
      snapshot(1, 118, 0.78, 3.1, "wide-b", 0.04, 0.62),
      snapshot(2, 42, 0.22, 0.9, "shortcut", 0.31, 0.55),
      snapshot(3, 39, 0.2, 0.8, "shortcut", 0.34, 0.54),
    ],
    changes: [
      {
        changeId: "hint-set-v2",
        generationIndex: 2,
        kind: "hint",
        sourceComponent: "soft_hints",
        sourceSpan: "hint:force-short-route",
      },
    ],
  };
}

describe("exploration collapse guard", () => {
  it("detects advisory-only collapse after guidance", async () => {
    const { detectExplorationCollapse, renderExplorationCollapseReport } = await protocol();
    const { snapshots, changes } = collapsedRun();

    const report = detectExplorationCollapse(snapshots, changes, { advisoryOnly: true });

    expect(report.events).toHaveLength(1);
    const event = report.events[0];
    expect(event.guidanceChange).toMatchObject({
      changeId: "hint-set-v2",
      sourceComponent: "soft_hints",
      sourceSpan: "hint:force-short-route",
    });
    expect(event.advisoryOnly).toBe(true);
    expect(event.mitigation).toBe("none");
    expect(event.signals.map((signal: { metric: string }) => signal.metric)).toEqual(
      expect.arrayContaining([
        "response_length",
        "diversity",
        "entropy",
        "route_repetition",
        "rollback_rate",
      ]),
    );

    const rendered = renderExplorationCollapseReport(report);
    expect(rendered).toContain("hint-set-v2");
    expect(rendered).toContain("soft_hints");
    expect(rendered).toContain("hint:force-short-route");
  });

  it("keeps automatic mitigation opt-in", async () => {
    const { detectExplorationCollapse } = await protocol();
    const { snapshots, changes } = collapsedRun();

    const advisory = detectExplorationCollapse(snapshots, changes, { advisoryOnly: true });
    const auto = detectExplorationCollapse(snapshots, changes, {
      advisoryOnly: false,
      autoMitigation: true,
    });

    expect(advisory.events[0].mitigation).toBe("none");
    expect(auto.events[0].mitigation).toBe("demote_guidance");
    expect(auto.records[0]).toMatchObject({
      event_type: "exploration_collapse_detected",
      payload: { guidance_change: { change_id: "hint-set-v2" } },
    });
  });

  it("ignores JSON null metrics instead of coercing them to zero", async () => {
    const { detectExplorationCollapse } = await protocol();
    const snapshots = JSON.parse(
      JSON.stringify([
        {
          generationIndex: 0,
          responseLength: 100,
          diversity: 0.8,
          entropy: 3.2,
          routeSignature: "wide-a",
          rollbackRate: 0,
          score: 0.6,
        },
        {
          generationIndex: 1,
          responseLength: 100,
          diversity: 0.7,
          entropy: 3.0,
          routeSignature: "wide-b",
          rollbackRate: 0,
          score: 0.61,
        },
        {
          generationIndex: 2,
          responseLength: 100,
          diversity: null,
          entropy: null,
          routeSignature: "wide-c",
          rollbackRate: 0,
          score: 0.61,
        },
      ]),
    ) as ExplorationSnapshot[];
    const changes: GuidanceChange[] = [
      {
        changeId: "hint-v2",
        generationIndex: 2,
        kind: "hint",
        sourceComponent: "soft_hints",
      },
    ];

    expect(detectExplorationCollapse(snapshots, changes).events).toEqual([]);
  });

  it("does not warn without an exploration drop", async () => {
    const { detectExplorationCollapse } = await protocol();
    const snapshots = [
      snapshot(0, 100, 0.5, 2.0, "a", 0.1, 0.5),
      snapshot(1, 102, 0.52, 2.1, "b", 0.08, 0.51),
      snapshot(2, 99, 0.51, 2.0, "c", 0.09, 0.52),
      snapshot(3, 101, 0.5, 2.0, "d", 0.1, 0.53),
    ];
    const changes: GuidanceChange[] = [
      {
        changeId: "teacher-v1",
        generationIndex: 2,
        kind: "teacher_signal",
        sourceComponent: "teacher",
      },
    ];

    expect(detectExplorationCollapse(snapshots, changes).events).toEqual([]);
  });
});
