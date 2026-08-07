import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { asDbPath, asRunId, asScenarioName } from "../src/domain/ids.js";
import { ArtifactStore } from "../src/knowledge/artifact-store.js";
import { buildKnowledgeApiRoutes } from "../src/server/knowledge-api.js";
import { StartRunCmdSchema } from "../src/server/protocol.js";

const SCENARIO = asScenarioName("grid_ctf");

function root(): string {
  return mkdtempSync(join(tmpdir(), "playbook-approval-"));
}

function store(dir: string): ArtifactStore {
  return new ArtifactStore({ runsRoot: join(dir, "runs"), knowledgeRoot: join(dir, "knowledge") });
}

describe("playbook approval gate", () => {
  it("accepts the playbook approval flag and rejects the removed lesson alias", () => {
    expect(
      StartRunCmdSchema.parse({
        type: "start_run",
        scenario: "grid_ctf",
        generations: 1,
        require_playbook_approval: true,
      }).require_playbook_approval,
    ).toBe(true);
    // The deprecated require_lesson_approval alias was removed; the strict schema rejects it.
    expect(() =>
      StartRunCmdSchema.parse({
        type: "start_run",
        scenario: "grid_ctf",
        generations: 1,
        require_lesson_approval: true,
      }),
    ).toThrow();
  });

  it("defaults off and writes playbooks live", () => {
    const dir = root();
    try {
      const artifacts = store(dir);
      artifacts.writePlaybook(SCENARIO, "approved playbook");

      const result = artifacts.writeOrStagePlaybook(SCENARIO, "pending playbook", {
        requireApproval: false,
        sourceRunId: asRunId("run-approval"),
        generation: 2,
        curatorDecision: "advance",
      });

      expect(result).toBe("live");
      expect(artifacts.readPlaybook(SCENARIO)).toBe("pending playbook\n");
      expect(artifacts.readPendingPlaybook(SCENARIO).hasPending).toBe(false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("auto mode supersedes stale pending approvals", () => {
    const dir = root();
    try {
      const artifacts = store(dir);
      artifacts.writePlaybook(SCENARIO, "approved playbook");
      artifacts.writeOrStagePlaybook(SCENARIO, "pending old", {
        requireApproval: true,
        sourceRunId: asRunId("run-approval"),
        generation: 2,
        curatorDecision: "advance",
      });

      expect(
        artifacts.writeOrStagePlaybook(SCENARIO, "auto new", {
          requireApproval: false,
          sourceRunId: asRunId("run-auto"),
          generation: 3,
          curatorDecision: "advance",
        }),
      ).toBe("live");

      expect(artifacts.readPlaybook(SCENARIO)).toBe("auto new\n");
      expect(artifacts.readPendingPlaybook(SCENARIO).hasPending).toBe(false);
      expect(artifacts.approvePendingPlaybook(SCENARIO)).toEqual({
        ok: false,
        status: "missing",
      });
      expect(artifacts.readPlaybook(SCENARIO)).toBe("auto new\n");
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("skips new staging while an unresolved pending playbook exists", () => {
    const dir = root();
    try {
      const artifacts = store(dir);
      artifacts.writePlaybook(SCENARIO, "approved playbook");
      artifacts.writeOrStagePlaybook(SCENARIO, "pending playbook", {
        requireApproval: true,
        sourceRunId: asRunId("run-approval"),
        generation: 2,
        curatorDecision: "advance",
      });

      expect(
        artifacts.writeOrStagePlaybook(SCENARIO, "new pending playbook", {
          requireApproval: true,
          sourceRunId: asRunId("run-approval"),
          generation: 3,
          curatorDecision: "advance",
        }),
      ).toBe("awaiting_approval");
      expect(artifacts.readPendingPlaybook(SCENARIO).content).toBe("pending playbook\n");
      expect(artifacts.readPendingPlaybook(SCENARIO).provenance?.generation).toBe(2);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("stages pending playbooks without touching approved playbook", () => {
    const dir = root();
    try {
      const artifacts = store(dir);
      artifacts.writePlaybook(SCENARIO, "approved playbook");

      const result = artifacts.writeOrStagePlaybook(SCENARIO, "pending playbook", {
        requireApproval: true,
        sourceRunId: asRunId("run-approval"),
        generation: 2,
        curatorDecision: "advance",
      });

      expect(result).toBe("pending");
      expect(artifacts.readPlaybook(SCENARIO)).toBe("approved playbook\n");
      const pending = artifacts.readPendingPlaybook(SCENARIO);
      expect(pending.hasPending).toBe(true);
      expect(pending.content).toBe("pending playbook\n");
      expect(pending.diff).toContain("-approved playbook");
      expect(pending.diff).toContain("+pending playbook");
      expect(pending.provenance?.source_run_id).toBe("run-approval");
      expect(pending.provenance?.generation).toBe(2);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("approves or rejects pending playbooks without structured lesson side effects", () => {
    const dir = root();
    try {
      const artifacts = store(dir);
      artifacts.writePlaybook(SCENARIO, "approved playbook");
      artifacts.writeOrStagePlaybook(SCENARIO, "pending playbook", {
        requireApproval: true,
        sourceRunId: asRunId("run-approval"),
        generation: 2,
        curatorDecision: "advance",
      });

      expect(artifacts.approvePendingPlaybook(SCENARIO)).toEqual({
        ok: true,
        status: "approved",
      });
      expect(artifacts.readPlaybook(SCENARIO)).toBe("pending playbook\n");

      artifacts.writeOrStagePlaybook(SCENARIO, "rejected playbook", {
        requireApproval: true,
        sourceRunId: asRunId("run-approval"),
        generation: 3,
        curatorDecision: "advance",
      });

      expect(artifacts.rejectPendingPlaybook(SCENARIO)).toEqual({
        ok: true,
        status: "rejected",
      });
      expect(artifacts.readPlaybook(SCENARIO)).toBe("pending playbook\n");
      expect(existsSync(join(dir, "knowledge", "grid_ctf", "playbook.pending.md"))).toBe(false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("skips curator consolidation live writes while approval gate is active", async () => {
    const { GenerationRunner } = await import("../src/loop/generation-runner.js");
    const { GridCtfScenario } = await import("../src/scenarios/grid-ctf.js");
    const { SQLiteStore } = await import("../src/storage/index.js");

    const dir = root();
    const artifacts = store(dir);
    const livePlaybook =
      "<!-- PLAYBOOK_START -->\n## Strategy Updates\n\n- keep current plan\n<!-- PLAYBOOK_END -->\n\n" +
      "<!-- LESSONS_START -->\n- existing lesson\n<!-- LESSONS_END -->\n\n" +
      "<!-- COMPETITOR_HINTS_START -->\n- hint\n<!-- COMPETITOR_HINTS_END -->";
    artifacts.writePlaybook(SCENARIO, livePlaybook);

    class ConsolidatingProvider {
      readonly name = "consolidating";
      defaultModel(): string {
        return "consolidating-model";
      }
      async complete(opts: { userPrompt: string }) {
        if (opts.userPrompt.startsWith("Describe your strategy")) {
          return {
            text: JSON.stringify({ aggression: 0.6, defense: 0.55, path_bias: 0.5 }),
            model: "m",
            usage: {},
          };
        }
        if (opts.userPrompt.startsWith("You are a curator consolidating")) {
          return {
            text: "<!-- CONSOLIDATED_LESSONS_START -->\n- consolidated leak\n<!-- CONSOLIDATED_LESSONS_END -->\n<!-- LESSONS_REMOVED: 0 -->",
            model: "m",
            usage: {},
          };
        }
        return { text: "No playbook update.", model: "m", usage: {} };
      }
    }

    try {
      const dbPath = join(dir, "test.db");
      const storeDb = new SQLiteStore(asDbPath(dbPath));
      storeDb.migrate(join(import.meta.dirname, "..", "migrations"));
      const runner = new GenerationRunner({
        provider: new ConsolidatingProvider(),
        scenario: new GridCtfScenario(),
        store: storeDb,
        runsRoot: join(dir, "runs"),
        knowledgeRoot: join(dir, "knowledge"),
        matchesPerGeneration: 1,
        maxRetries: 0,
        minDelta: 0,
        curatorEnabled: true,
        curatorConsolidateEveryNGens: 1,
        requirePlaybookApproval: true,
      });

      await runner.run(asRunId("approval-consolidation"), 1);
      storeDb.close();

      expect(
        readFileSync(join(dir, "knowledge", "grid_ctf", "playbook.md"), "utf-8"),
      ).not.toContain("consolidated leak");
      expect(artifacts.readPendingPlaybook(SCENARIO).hasPending).toBe(false);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("exposes pending playbook approve/reject routes", () => {
    const dir = root();
    try {
      const artifacts = store(dir);
      artifacts.writePlaybook(SCENARIO, "approved playbook");
      artifacts.writeOrStagePlaybook(SCENARIO, "pending playbook", {
        requireApproval: true,
        sourceRunId: asRunId("run-approval"),
        generation: 2,
        curatorDecision: "advance",
      });
      const routes = buildKnowledgeApiRoutes({
        runsRoot: join(dir, "runs"),
        knowledgeRoot: join(dir, "knowledge"),
        skillsRoot: join(dir, "skills"),
        openStore: () => {
          throw new Error("store unused");
        },
        getSolveManager: () => ({
          submit: () => "job",
          getStatus: () => ({}),
          getResult: () => null,
        }),
      });

      expect(routes.pendingPlaybook(SCENARIO).status).toBe(200);
      expect((routes.pendingPlaybook(SCENARIO).body as { hasPending: boolean }).hasPending).toBe(
        true,
      );
      expect(routes.approvePendingPlaybook(SCENARIO)).toEqual({
        status: 200,
        body: { ok: true, status: "approved" },
      });
      expect(readFileSync(join(dir, "knowledge", "grid_ctf", "playbook.md"), "utf-8")).toBe(
        "pending playbook\n",
      );
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
