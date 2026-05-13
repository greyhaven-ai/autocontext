/**
 * Tests for AC-344: Knowledge System — Playbook, Artifacts, Trajectory, Context Budget.
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync, readFileSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function makeTempDir(): string {
  return mkdtempSync(join(tmpdir(), "ac-knowledge-"));
}

// ---------------------------------------------------------------------------
// VersionedFileStore
// ---------------------------------------------------------------------------

describe("VersionedFileStore", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("should be importable", async () => {
    const { VersionedFileStore } = await import("../src/knowledge/versioned-store.js");
    expect(VersionedFileStore).toBeDefined();
  });

  it("write and read a file", async () => {
    const { VersionedFileStore } = await import("../src/knowledge/versioned-store.js");
    const store = new VersionedFileStore(dir);
    store.write("test.md", "hello world");
    expect(store.read("test.md")).toBe("hello world");
  });

  it("read returns default when file missing", async () => {
    const { VersionedFileStore } = await import("../src/knowledge/versioned-store.js");
    const store = new VersionedFileStore(dir);
    expect(store.read("missing.md")).toBe("");
    expect(store.read("missing.md", "fallback")).toBe("fallback");
  });

  it("archives previous version on write", async () => {
    const { VersionedFileStore } = await import("../src/knowledge/versioned-store.js");
    const store = new VersionedFileStore(dir);
    store.write("test.md", "v1 content");
    store.write("test.md", "v2 content");
    expect(store.read("test.md")).toBe("v2 content");
    expect(store.versionCount("test.md")).toBe(1);
  });

  it("rollback restores previous version", async () => {
    const { VersionedFileStore } = await import("../src/knowledge/versioned-store.js");
    const store = new VersionedFileStore(dir);
    store.write("test.md", "v1 content");
    store.write("test.md", "v2 content");
    const success = store.rollback("test.md");
    expect(success).toBe(true);
    expect(store.read("test.md")).toBe("v1 content");
  });

  it("rollback returns false when no versions", async () => {
    const { VersionedFileStore } = await import("../src/knowledge/versioned-store.js");
    const store = new VersionedFileStore(dir);
    store.write("test.md", "only version");
    expect(store.rollback("test.md")).toBe(false);
  });

  it("prunes old versions beyond max", async () => {
    const { VersionedFileStore } = await import("../src/knowledge/versioned-store.js");
    const store = new VersionedFileStore(dir, { maxVersions: 3 });
    for (let i = 1; i <= 6; i++) {
      store.write("test.md", `version ${i}`);
    }
    expect(store.versionCount("test.md")).toBe(3);
    expect(store.read("test.md")).toBe("version 6");
  });

  it("keeps archive numbering monotonic after prune", async () => {
    const { VersionedFileStore } = await import("../src/knowledge/versioned-store.js");
    const store = new VersionedFileStore(dir, { maxVersions: 3 });
    for (let i = 1; i <= 6; i++) {
      store.write("test.md", `version ${i}`);
    }
    store.write("test.md", "version 7");

    expect(store.versionCount("test.md")).toBe(3);
    expect(store.readVersion("test.md", 6)).toBe("version 6");
  });

  it("readVersion reads specific archived version", async () => {
    const { VersionedFileStore } = await import("../src/knowledge/versioned-store.js");
    const store = new VersionedFileStore(dir);
    store.write("test.md", "v1");
    store.write("test.md", "v2");
    store.write("test.md", "v3");
    // Version 1 was archived when v2 was written
    expect(store.readVersion("test.md", 1)).toBe("v1");
    expect(store.readVersion("test.md", 2)).toBe("v2");
  });
});

// ---------------------------------------------------------------------------
// PlaybookManager
// ---------------------------------------------------------------------------

describe("PlaybookManager", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("should be importable", async () => {
    const { PlaybookManager } = await import("../src/knowledge/playbook.js");
    expect(PlaybookManager).toBeDefined();
  });

  it("read returns sentinel when no playbook", async () => {
    const { PlaybookManager, EMPTY_PLAYBOOK_SENTINEL } = await import("../src/knowledge/playbook.js");
    const mgr = new PlaybookManager(dir);
    expect(mgr.read("grid_ctf")).toBe(EMPTY_PLAYBOOK_SENTINEL);
  });

  it("write and read playbook", async () => {
    const { PlaybookManager } = await import("../src/knowledge/playbook.js");
    const mgr = new PlaybookManager(dir);
    mgr.write("grid_ctf", "# Playbook\n\nBe aggressive.");
    const content = mgr.read("grid_ctf");
    expect(content).toContain("Be aggressive");
  });

  it("versioning: write creates archive", async () => {
    const { PlaybookManager } = await import("../src/knowledge/playbook.js");
    const mgr = new PlaybookManager(dir);
    mgr.write("grid_ctf", "v1");
    mgr.write("grid_ctf", "v2");
    expect(mgr.versionCount("grid_ctf")).toBe(1);
  });

  it("rollback restores previous playbook", async () => {
    const { PlaybookManager } = await import("../src/knowledge/playbook.js");
    const mgr = new PlaybookManager(dir);
    mgr.write("grid_ctf", "v1 playbook");
    mgr.write("grid_ctf", "v2 playbook");
    const ok = mgr.rollback("grid_ctf");
    expect(ok).toBe(true);
    expect(mgr.read("grid_ctf")).toContain("v1 playbook");
  });

  it("exports PLAYBOOK_MARKERS", async () => {
    const { PLAYBOOK_MARKERS } = await import("../src/knowledge/playbook.js");
    expect(PLAYBOOK_MARKERS.PLAYBOOK_START).toBe("<!-- PLAYBOOK_START -->");
    expect(PLAYBOOK_MARKERS.PLAYBOOK_END).toBe("<!-- PLAYBOOK_END -->");
    expect(PLAYBOOK_MARKERS.LESSONS_START).toBe("<!-- LESSONS_START -->");
    expect(PLAYBOOK_MARKERS.LESSONS_END).toBe("<!-- LESSONS_END -->");
    expect(PLAYBOOK_MARKERS.HINTS_START).toBe("<!-- COMPETITOR_HINTS_START -->");
    expect(PLAYBOOK_MARKERS.HINTS_END).toBe("<!-- COMPETITOR_HINTS_END -->");
  });
});

// ---------------------------------------------------------------------------
// PlaybookGuard
// ---------------------------------------------------------------------------

describe("PlaybookGuard", () => {
  it("should be importable", async () => {
    const { PlaybookGuard } = await import("../src/knowledge/playbook.js");
    expect(PlaybookGuard).toBeDefined();
  });

  it("approves valid update", async () => {
    const { PlaybookGuard } = await import("../src/knowledge/playbook.js");
    const guard = new PlaybookGuard();
    const result = guard.check("old content", "new content that is similar length");
    expect(result.approved).toBe(true);
  });

  it("rejects empty proposed on non-empty current", async () => {
    const { PlaybookGuard } = await import("../src/knowledge/playbook.js");
    const guard = new PlaybookGuard();
    const result = guard.check("existing content", "");
    expect(result.approved).toBe(false);
    expect(result.reason).toContain("empty");
  });

  it("rejects excessive shrinkage", async () => {
    const { PlaybookGuard } = await import("../src/knowledge/playbook.js");
    const guard = new PlaybookGuard(0.3);
    const result = guard.check("a".repeat(100), "short");
    expect(result.approved).toBe(false);
    expect(result.reason).toContain("shrink");
  });

  it("rejects missing required markers", async () => {
    const { PlaybookGuard, PLAYBOOK_MARKERS } = await import("../src/knowledge/playbook.js");
    const guard = new PlaybookGuard();
    const current = `${PLAYBOOK_MARKERS.PLAYBOOK_START}\ncontent\n${PLAYBOOK_MARKERS.PLAYBOOK_END}`;
    // Proposed must be long enough to pass shrinkage check but missing markers
    const proposed = "no markers here — ".repeat(10);
    const result = guard.check(current, proposed);
    expect(result.approved).toBe(false);
    expect(result.reason).toContain("marker");
  });
});

// ---------------------------------------------------------------------------
// ArtifactStore
// ---------------------------------------------------------------------------

describe("ArtifactStore", () => {
  let dir: string;

  beforeEach(() => { dir = makeTempDir(); });
  afterEach(() => { rmSync(dir, { recursive: true, force: true }); });

  it("should be importable", async () => {
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    expect(ArtifactStore).toBeDefined();
  });

  it("writeJson creates file with formatted JSON", async () => {
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const store = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    const path = join(dir, "test.json");
    store.writeJson(path, { key: "value", num: 42 });
    const content = JSON.parse(readFileSync(path, "utf-8"));
    expect(content.key).toBe("value");
    expect(content.num).toBe(42);
  });

  it("writeMarkdown creates file", async () => {
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const store = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    const path = join(dir, "test.md");
    store.writeMarkdown(path, "# Hello\n\nContent");
    const content = readFileSync(path, "utf-8");
    expect(content).toContain("# Hello");
  });

  it("appendMarkdown appends with heading", async () => {
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const store = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    const path = join(dir, "log.md");
    store.appendMarkdown(path, "First entry", "Gen 1");
    store.appendMarkdown(path, "Second entry", "Gen 2");
    const content = readFileSync(path, "utf-8");
    expect(content).toContain("## Gen 1");
    expect(content).toContain("## Gen 2");
    expect(content).toContain("Second entry");
  });

  it("generationDir returns correct path", async () => {
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const store = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    const genDir = store.generationDir("run-1", 3);
    expect(genDir).toContain("run-1");
    expect(genDir).toContain("gen_3");
  });

  it("readPlaybook/writePlaybook delegates to PlaybookManager", async () => {
    const { ArtifactStore } = await import("../src/knowledge/artifact-store.js");
    const store = new ArtifactStore({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
    });
    store.writePlaybook("grid_ctf", "# Strategy\nBe aggressive.");
    const content = store.readPlaybook("grid_ctf");
    expect(content).toContain("Be aggressive");
  });
});

// ---------------------------------------------------------------------------
// ScoreTrajectoryBuilder
// ---------------------------------------------------------------------------

describe("ScoreTrajectoryBuilder", () => {
  it("should be importable", async () => {
    const { ScoreTrajectoryBuilder } = await import("../src/knowledge/trajectory.js");
    expect(ScoreTrajectoryBuilder).toBeDefined();
  });

  it("returns empty string for no data", async () => {
    const { ScoreTrajectoryBuilder } = await import("../src/knowledge/trajectory.js");
    const builder = new ScoreTrajectoryBuilder([]);
    expect(builder.build()).toBe("");
  });

  it("builds markdown table from trajectory data", async () => {
    const { ScoreTrajectoryBuilder } = await import("../src/knowledge/trajectory.js");
    const data = [
      { generation_index: 1, mean_score: 0.50, best_score: 0.55, elo: 1000, gate_decision: "retry", delta: 0.55, scoring_backend: "elo", rating_uncertainty: null },
      { generation_index: 2, mean_score: 0.65, best_score: 0.70, elo: 1050, gate_decision: "advance", delta: 0.15, scoring_backend: "elo", rating_uncertainty: null },
    ];
    const builder = new ScoreTrajectoryBuilder(data);
    const md = builder.build();
    expect(md).toContain("## Score Trajectory");
    expect(md).toContain("| Gen |");
    expect(md).toContain("| Elo |");
    expect(md).toContain("retry");
    expect(md).toContain("advance");
    expect(md).toContain("0.5500");
  });

  it("uses 'Rating' label for non-elo backend", async () => {
    const { ScoreTrajectoryBuilder } = await import("../src/knowledge/trajectory.js");
    const data = [
      { generation_index: 1, mean_score: 0.50, best_score: 0.55, elo: 1000, gate_decision: "retry", delta: 0.55, scoring_backend: "glicko", rating_uncertainty: 75.0 },
    ];
    const builder = new ScoreTrajectoryBuilder(data);
    const md = builder.build();
    expect(md).toContain("| Rating |");
    expect(md).toContain("| Uncertainty |");
    expect(md).toContain("Backend: `glicko`");
  });

  it("shows signed delta values", async () => {
    const { ScoreTrajectoryBuilder } = await import("../src/knowledge/trajectory.js");
    const data = [
      { generation_index: 1, mean_score: 0.70, best_score: 0.75, elo: 1050, gate_decision: "advance", delta: 0.75, scoring_backend: "elo", rating_uncertainty: null },
      { generation_index: 2, mean_score: 0.60, best_score: 0.65, elo: 1020, gate_decision: "rollback", delta: -0.10, scoring_backend: "elo", rating_uncertainty: null },
    ];
    const builder = new ScoreTrajectoryBuilder(data);
    const md = builder.build();
    expect(md).toContain("+0.75");
    expect(md).toContain("-0.10");
  });

  it("renders dimension trajectory from dimension_summary.best_dimensions", async () => {
    const { ScoreTrajectoryBuilder } = await import("../src/knowledge/trajectory.js");
    const data = [
      {
        generation_index: 1,
        mean_score: 0.5,
        best_score: 0.6,
        elo: 1000,
        gate_decision: "retry",
        delta: 0.6,
        dimension_summary: {
          best_dimensions: {
            capture_progress: 0.55,
            defender_survival: 0.72,
          },
        },
        scoring_backend: "elo",
        rating_uncertainty: null,
      },
      {
        generation_index: 2,
        mean_score: 0.7,
        best_score: 0.8,
        elo: 1020,
        gate_decision: "advance",
        delta: 0.2,
        dimension_summary: {
          best_dimensions: {
            capture_progress: 0.81,
            defender_survival: 0.68,
          },
        },
        scoring_backend: "elo",
        rating_uncertainty: null,
      },
    ];
    const builder = new ScoreTrajectoryBuilder(data);
    const md = builder.build();

    expect(md).toContain("## Dimension Trajectory (Best Match)");
    expect(md).toContain("capture_progress");
    expect(md).toContain("defender_survival");
    expect(md).toContain("0.5500");
    expect(md).toContain("0.8100");
  });
});

// ---------------------------------------------------------------------------
// ContextBudget
// ---------------------------------------------------------------------------

describe("ContextBudget", () => {
  it("should be importable", async () => {
    const { ContextBudget } = await import("../src/prompts/context-budget.js");
    expect(ContextBudget).toBeDefined();
  });

  it("estimateTokens uses char/4 heuristic", async () => {
    const { estimateTokens } = await import("../src/prompts/context-budget.js");
    expect(estimateTokens("1234")).toBe(1);
    expect(estimateTokens("12345678")).toBe(2);
    expect(estimateTokens("")).toBe(0);
  });

  it("apply returns components unchanged when within budget", async () => {
    const { ContextBudget } = await import("../src/prompts/context-budget.js");
    const budget = new ContextBudget(100_000);
    const components = {
      playbook: "short text",
      hints: "some hints",
      trajectory: "gen table",
    };
    const result = budget.apply(components);
    expect(result).toEqual(components);
  });

  it("apply trims least-critical components first", async () => {
    const { ContextBudget } = await import("../src/prompts/context-budget.js");
    // Very tight budget
    const budget = new ContextBudget(10);
    const components = {
      trajectory: "x".repeat(100),
      playbook: "y".repeat(100),
      hints: "z".repeat(100),
    };
    const result = budget.apply(components);
    // trajectory should be trimmed first, then playbook
    expect(result.trajectory.length).toBeLessThan(100);
    // hints should be protected
    expect(result.hints).toBe(components.hints);
  });

  it("never trims protected components (hints, dead_ends)", async () => {
    const { ContextBudget } = await import("../src/prompts/context-budget.js");
    const budget = new ContextBudget(5);
    const components = {
      hints: "a".repeat(200),
      dead_ends: "b".repeat(200),
      trajectory: "c".repeat(200),
    };
    const result = budget.apply(components);
    expect(result.hints).toBe(components.hints);
    expect(result.dead_ends).toBe(components.dead_ends);
  });

  it("returns copy when budget is 0 (disabled)", async () => {
    const { ContextBudget } = await import("../src/prompts/context-budget.js");
    const budget = new ContextBudget(0);
    const components = { playbook: "some content" };
    const result = budget.apply(components);
    expect(result).toEqual(components);
  });

  it("truncated text ends with truncation marker when the marker fits the budget", async () => {
    const { ContextBudget } = await import("../src/prompts/context-budget.js");
    const budget = new ContextBudget(20);
    const components = {
      trajectory: "a".repeat(400),
    };
    const result = budget.apply(components);
    expect(result.trajectory).toContain("[... truncated for context budget ...]");
  });

  it("deduplicates equivalent components using canonical policy order", async () => {
    const { ContextBudget } = await import("../src/prompts/context-budget.js");
    const duplicate = "Use the stable rollback guard.";
    const budget = new ContextBudget(1000);
    const result = budget.apply({
      playbook: duplicate,
      analysis: duplicate,
      trajectory: "Gen 1: 0.5",
      hints: duplicate,
    });

    expect(result.playbook).toBe(duplicate);
    expect(result.analysis).toBe("");
    expect(result.hints).toBe(duplicate);
  });

  it("does not deduplicate role-scoped components globally", async () => {
    const { ContextBudget } = await import("../src/prompts/context-budget.js");
    const duplicate = "Role-scoped evidence that multiple roles should receive.";
    const budget = new ContextBudget(1000);
    const components = {
      evidence_manifest_analyst: duplicate,
      evidence_manifest_architect: duplicate,
      notebook_analyst: duplicate,
      notebook_architect: duplicate,
    };
    const result = budget.apply(components);

    expect(result).toEqual(components);
  });

  it("applies component caps before global trimming", async () => {
    const { ContextBudget, ContextBudgetPolicy, estimateTokens } = await import("../src/prompts/context-budget.js");
    const budget = new ContextBudget(
      1000,
      new ContextBudgetPolicy({ componentTokenCaps: { analysis: 5 } }),
    );
    const result = budget.apply({
      playbook: "small playbook",
      analysis: "A".repeat(200),
    });

    expect(result.playbook).toBe("small playbook");
    expect(result.analysis.length).toBeLessThan(200);
    expect(estimateTokens(result.analysis)).toBeLessThanOrEqual(5);
  });

  it("lets policy override trim order and protected components", async () => {
    const { ContextBudget, ContextBudgetPolicy } = await import("../src/prompts/context-budget.js");
    const budget = new ContextBudget(
      10,
      new ContextBudgetPolicy({
        trimOrder: ["playbook", "analysis"],
        protectedComponents: ["analysis"],
        componentTokenCaps: {},
      }),
    );
    const result = budget.apply({
      playbook: "P".repeat(200),
      analysis: "A".repeat(200),
    });

    expect(result.playbook.length).toBeLessThan(200);
    expect(result.analysis).toBe("A".repeat(200));
  });
});
