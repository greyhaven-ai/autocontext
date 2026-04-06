/**
 * AC-504: Evidence workspace tests (TypeScript).
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  mkdtempSync,
  mkdirSync,
  unlinkSync,
  rmSync,
  writeFileSync,
  existsSync,
  readFileSync,
} from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import {
  getArtifact,
  listByKind,
  type EvidenceArtifact,
  type EvidenceWorkspace,
} from "../src/evidence/workspace.js";
import {
  materializeWorkspace,
  scanRunArtifacts,
  scanKnowledgeArtifacts,
} from "../src/evidence/materializer.js";
import {
  renderEvidenceManifest,
  renderArtifactDetail,
} from "../src/evidence/manifest.js";
import {
  recordAccess,
  saveAccessLog,
  loadAccessLog,
  computeUtilization,
} from "../src/evidence/tracker.js";

function makeArtifact(
  overrides: Partial<EvidenceArtifact> = {},
): EvidenceArtifact {
  return {
    artifactId: "test_abc123",
    sourceRunId: "run_001",
    kind: "trace",
    path: "test_abc123_events.ndjson",
    summary: "trace: events.ndjson from run_001",
    sizeBytes: 1024,
    generation: 1,
    ...overrides,
  };
}

function makeWorkspace(
  overrides: Partial<EvidenceWorkspace> = {},
): EvidenceWorkspace {
  const artifacts = (overrides.artifacts as EvidenceArtifact[]) ?? [
    makeArtifact(),
  ];
  return {
    workspaceDir: "/tmp/test_workspace",
    sourceRuns: ["run_001"],
    artifacts,
    totalSizeBytes: artifacts.reduce((s, a) => s + a.sizeBytes, 0),
    materializedAt: "2026-04-06T00:00:00Z",
    accessedArtifacts: [],
    ...overrides,
  };
}

let evidenceTmp: string;

beforeEach(() => {
  evidenceTmp = mkdtempSync(join(tmpdir(), "ac504-test-"));
  // Run artifacts
  const runDir = join(evidenceTmp, "runs", "run_001");
  mkdirSync(runDir, { recursive: true });
  writeFileSync(join(runDir, "events.ndjson"), '{"event":"start"}\n');
  const genDir = join(runDir, "gen_1");
  mkdirSync(genDir);
  writeFileSync(join(genDir, "analyst_output.md"), "# Analysis\nFindings.");
  writeFileSync(join(genDir, "gate_decision.json"), '{"decision":"advance"}');

  // Knowledge artifacts
  const kDir = join(evidenceTmp, "knowledge", "test_scenario");
  mkdirSync(kDir, { recursive: true });
  writeFileSync(join(kDir, "playbook.md"), "# Playbook\nStep 1.");
  writeFileSync(join(kDir, "dead_ends.md"), "# Dead Ends\nApproach X failed.");
  mkdirSync(join(kDir, "tools"));
  writeFileSync(join(kDir, "tools", "validator.py"), "def validate(): pass");
  mkdirSync(join(kDir, "analysis"));
  writeFileSync(join(kDir, "analysis", "gen_1.md"), "Gen 1 analysis.");
});

afterEach(() => {
  rmSync(evidenceTmp, { recursive: true, force: true });
});

describe("Workspace model", () => {
  it("getArtifact returns correct artifact by ID", () => {
    const a = makeArtifact({ artifactId: "abc123" });
    const ws = makeWorkspace({ artifacts: [a] });
    expect(getArtifact(ws, "abc123")).toBe(a);
  });

  it("getArtifact returns null for missing ID", () => {
    const ws = makeWorkspace();
    expect(getArtifact(ws, "nonexistent")).toBeNull();
  });

  it("listByKind filters correctly", () => {
    const ws = makeWorkspace({
      artifacts: [
        makeArtifact({ artifactId: "a1", kind: "trace" }),
        makeArtifact({ artifactId: "a2", kind: "gate_decision" }),
        makeArtifact({ artifactId: "a3", kind: "trace" }),
      ],
    });
    const traces = listByKind(ws, "trace");
    expect(traces).toHaveLength(2);
    expect(traces.every((t) => t.kind === "trace")).toBe(true);
  });
});

describe("Materializer", () => {
  it("creates workspace directory", () => {
    const wsDir = join(evidenceTmp, "workspace");
    materializeWorkspace({
      knowledgeRoot: join(evidenceTmp, "knowledge"),
      runsRoot: join(evidenceTmp, "runs"),
      sourceRunIds: ["run_001"],
      workspaceDir: wsDir,
      scenarioName: "test_scenario",
    });
    expect(existsSync(wsDir)).toBe(true);
  });

  it("copies artifacts into workspace", () => {
    const wsDir = join(evidenceTmp, "workspace");
    const ws = materializeWorkspace({
      knowledgeRoot: join(evidenceTmp, "knowledge"),
      runsRoot: join(evidenceTmp, "runs"),
      sourceRunIds: ["run_001"],
      workspaceDir: wsDir,
      scenarioName: "test_scenario",
    });
    expect(ws.artifacts.length).toBeGreaterThan(0);
    for (const a of ws.artifacts) {
      expect(existsSync(join(wsDir, a.path))).toBe(true);
    }
  });

  it("respects budget limit", () => {
    const wsDir = join(evidenceTmp, "workspace");
    const ws = materializeWorkspace({
      knowledgeRoot: join(evidenceTmp, "knowledge"),
      runsRoot: join(evidenceTmp, "runs"),
      sourceRunIds: ["run_001"],
      workspaceDir: wsDir,
      budgetBytes: 100,
      scenarioName: "test_scenario",
    });
    expect(ws.totalSizeBytes).toBeLessThanOrEqual(100);
  });

  it("handles empty run directories", () => {
    const wsDir = join(evidenceTmp, "workspace_empty");
    const ws = materializeWorkspace({
      knowledgeRoot: join(evidenceTmp, "knowledge"),
      runsRoot: join(evidenceTmp, "runs"),
      sourceRunIds: ["nonexistent_run"],
      workspaceDir: wsDir,
    });
    expect(ws.artifacts).toBeDefined();
  });

  it("writes manifest.json", () => {
    const wsDir = join(evidenceTmp, "workspace");
    materializeWorkspace({
      knowledgeRoot: join(evidenceTmp, "knowledge"),
      runsRoot: join(evidenceTmp, "runs"),
      sourceRunIds: ["run_001"],
      workspaceDir: wsDir,
      scenarioName: "test_scenario",
    });
    expect(existsSync(join(wsDir, "manifest.json"))).toBe(true);
    const manifest = JSON.parse(
      readFileSync(join(wsDir, "manifest.json"), "utf-8"),
    );
    expect(manifest.artifacts).toBeDefined();
  });

  it("removes stale files when rematerializing a reused workspace", () => {
    const wsDir = join(evidenceTmp, "workspace");
    const first = materializeWorkspace({
      knowledgeRoot: join(evidenceTmp, "knowledge"),
      runsRoot: join(evidenceTmp, "runs"),
      sourceRunIds: ["run_001"],
      workspaceDir: wsDir,
    });
    const traceArtifact = first.artifacts.find((a) => a.kind === "trace");
    expect(traceArtifact).toBeDefined();
    const stalePath = join(wsDir, traceArtifact!.path);
    expect(existsSync(stalePath)).toBe(true);

    unlinkSync(join(evidenceTmp, "runs", "run_001", "events.ndjson"));

    const second = materializeWorkspace({
      knowledgeRoot: join(evidenceTmp, "knowledge"),
      runsRoot: join(evidenceTmp, "runs"),
      sourceRunIds: ["run_001"],
      workspaceDir: wsDir,
    });
    expect(second.artifacts.every((a) => a.kind !== "trace")).toBe(true);
    expect(existsSync(stalePath)).toBe(false);
  });

  it("scanKnowledgeArtifacts finds playbook and tools", () => {
    const wsDir = join(evidenceTmp, "workspace");
    const ws = materializeWorkspace({
      knowledgeRoot: join(evidenceTmp, "knowledge"),
      runsRoot: join(evidenceTmp, "runs"),
      sourceRunIds: [],
      workspaceDir: wsDir,
      scenarioName: "test_scenario",
    });
    const kinds = new Set(ws.artifacts.map((a) => a.kind));
    expect(kinds.has("report")).toBe(true);
    expect(kinds.has("tool")).toBe(true);
  });
});

describe("Manifest", () => {
  it("includes artifact counts per kind", () => {
    const ws = makeWorkspace({
      artifacts: [
        makeArtifact({ artifactId: "a1", kind: "trace" }),
        makeArtifact({ artifactId: "a2", kind: "trace" }),
        makeArtifact({ artifactId: "a3", kind: "gate_decision" }),
      ],
    });
    const output = renderEvidenceManifest(ws);
    expect(output).toContain("Traces");
    expect(output).toContain("Gate decisions");
  });

  it("includes total size", () => {
    const ws = makeWorkspace();
    ws.totalSizeBytes = 5 * 1024 * 1024;
    const output = renderEvidenceManifest(ws);
    expect(output).toContain("5");
  });

  it("includes source run count", () => {
    const ws = makeWorkspace({ sourceRuns: ["run_001", "run_002"] });
    const output = renderEvidenceManifest(ws);
    expect(output).toContain("2 prior run");
  });

  it("renderArtifactDetail reads content", () => {
    const tmp = mkdtempSync(join(tmpdir(), "ac504-detail-"));
    try {
      writeFileSync(join(tmp, "test_file.md"), "Hello evidence!");
      const result = renderArtifactDetail(
        makeArtifact({ path: "test_file.md" }),
        tmp,
      );
      expect(result).toContain("Hello evidence!");
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });

  it("renderArtifactDetail handles missing file", () => {
    const result = renderArtifactDetail(
      makeArtifact({ path: "nonexistent.md" }),
      "/tmp/does_not_exist",
    );
    expect(result.toLowerCase()).toContain("not found");
  });
});

describe("Tracker", () => {
  it("recordAccess adds to accessed list", () => {
    const ws = makeWorkspace();
    recordAccess(ws, "abc123");
    expect(ws.accessedArtifacts).toContain("abc123");
  });

  it("recordAccess deduplicates", () => {
    const ws = makeWorkspace();
    recordAccess(ws, "abc123");
    recordAccess(ws, "abc123");
    expect(ws.accessedArtifacts.filter((id) => id === "abc123")).toHaveLength(
      1,
    );
  });

  it("save and load roundtrips", () => {
    const tmp = mkdtempSync(join(tmpdir(), "ac504-tracker-"));
    try {
      const ws = makeWorkspace({ workspaceDir: tmp });
      recordAccess(ws, "a1");
      recordAccess(ws, "a2");
      saveAccessLog(ws);
      const loaded = loadAccessLog(tmp);
      expect(loaded).toEqual(["a1", "a2"]);
    } finally {
      rmSync(tmp, { recursive: true, force: true });
    }
  });

  it("utilization counts correctly", () => {
    const ws = makeWorkspace({
      artifacts: [
        makeArtifact({ artifactId: "a1", kind: "trace" }),
        makeArtifact({ artifactId: "a2", kind: "gate_decision" }),
        makeArtifact({ artifactId: "a3", kind: "trace" }),
      ],
    });
    recordAccess(ws, "a1");
    recordAccess(ws, "a2");
    const stats = computeUtilization(ws);
    expect(stats.totalArtifacts).toBe(3);
    expect(stats.accessedCount).toBe(2);
    expect(stats.utilizationPercent).toBeCloseTo(66.7, 0);
  });

  it("utilization is zero when nothing accessed", () => {
    const ws = makeWorkspace();
    const stats = computeUtilization(ws);
    expect(stats.accessedCount).toBe(0);
    expect(stats.utilizationPercent).toBe(0);
  });
});
