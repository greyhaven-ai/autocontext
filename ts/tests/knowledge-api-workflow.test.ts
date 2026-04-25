import { existsSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { buildKnowledgeApiRoutes } from "../src/server/knowledge-api.js";

describe("knowledge API workflow", () => {
  it("forwards REST solve controls to the solve manager", () => {
    const submissions: Array<{
      description: string;
      generations: number;
      opts?: {
        familyOverride?: string;
        generationTimeBudgetSeconds?: number | null;
      };
    }> = [];
    const routes = buildKnowledgeApiRoutes({
      runsRoot: "/unused/runs",
      knowledgeRoot: "/unused/knowledge",
      skillsRoot: "/unused/skills",
      openStore: () => {
        throw new Error("store should not be opened for solve submission");
      },
      getSolveManager: () => ({
        submit: (description, generations, opts) => {
          submissions.push({ description, generations, opts });
          return "job_123";
        },
        getStatus: () => ({ status: "not_found" }),
        getResult: () => null,
      }),
    });

    const response = routes.submitSolve({
      description: " investigate checkout failures ",
      generations: 2,
      family: "investigation",
      generation_time_budget: 9,
    });

    expect(response).toEqual({ status: 200, body: { job_id: "job_123", status: "pending" } });
    expect(submissions).toEqual([
      {
        description: "investigate checkout failures",
        generations: 2,
        opts: {
          familyOverride: "investigation",
          generationTimeBudgetSeconds: 9,
        },
      },
    ]);
  });

  it("rejects invalid REST solve controls before submission", () => {
    let submitted = false;
    const routes = buildKnowledgeApiRoutes({
      runsRoot: "/unused/runs",
      knowledgeRoot: "/unused/knowledge",
      skillsRoot: "/unused/skills",
      openStore: () => {
        throw new Error("store should not be opened for solve submission");
      },
      getSolveManager: () => ({
        submit: () => {
          submitted = true;
          return "job_123";
        },
        getStatus: () => ({ status: "not_found" }),
        getResult: () => null,
      }),
    });

    const response = routes.submitSolve({
      description: "investigate checkout failures",
      generationTimeBudgetSeconds: "9",
    });

    expect(response).toEqual({
      status: 422,
      body: { error: "generationTimeBudgetSeconds must be a non-negative integer" },
    });
    expect(submitted).toBe(false);
  });

  it("rejects import packages whose scenario escapes knowledge and skills roots", () => {
    const dir = mkdtempSync(join(tmpdir(), "ac-knowledge-api-"));
    const routes = buildKnowledgeApiRoutes({
      runsRoot: join(dir, "runs"),
      knowledgeRoot: join(dir, "knowledge"),
      skillsRoot: join(dir, "skills"),
      openStore: () => {
        throw new Error("store should not be opened for package import");
      },
      getSolveManager: () => ({
        submit: () => "job_123",
        getStatus: () => ({ status: "not_found" }),
        getResult: () => null,
      }),
    });

    const response = routes.importPackage({
      package: {
        scenario_name: "../outside",
        playbook: "# should not be written",
        skill_markdown: "# should not be written",
      },
      conflict_policy: "overwrite",
    });

    expect(response.status).toBe(422);
    expect((response.body as Record<string, unknown>).detail).toContain("scenario");
    expect(existsSync(join(dir, "outside", "playbook.md"))).toBe(false);
    expect(existsSync(join(dir, "outside-ops", "SKILL.md"))).toBe(false);
  });
});
