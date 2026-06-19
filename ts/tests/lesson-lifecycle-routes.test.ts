import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { ArtifactStore } from "../src/knowledge/artifact-store.js";
import { buildKnowledgeApiRoutes, type KnowledgeApiRoutes } from "../src/server/knowledge-api.js";

function root(): string {
  return mkdtempSync(join(tmpdir(), "lesson-lifecycle-routes-"));
}

function routesFor(dir: string): KnowledgeApiRoutes {
  return buildKnowledgeApiRoutes({
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
}

/** Write a playbook whose LESSONS block carries the given bullets. */
function seedLessons(dir: string, scenario: string, bullets: string[]): void {
  const artifacts = new ArtifactStore({
    runsRoot: join(dir, "runs"),
    knowledgeRoot: join(dir, "knowledge"),
  });
  const block = ["<!-- LESSONS_START -->", ...bullets.map((b) => `- ${b}`), "<!-- LESSONS_END -->"];
  artifacts.writePlaybook(scenario, `Strategy.\n\n${block.join("\n")}\n`);
}

describe("lesson lifecycle routes", () => {
  it("lessonLifecycle derives active lessons from the playbook markdown", () => {
    const dir = root();
    try {
      seedLessons(dir, "grid_ctf", ["use X", "use Y"]);
      const res = routesFor(dir).lessonLifecycle("grid_ctf");
      expect(res.status).toBe(200);
      const body = res.body as { active: Array<{ id: string; text: string }>; pending: unknown[] };
      expect(body.pending).toEqual([]);
      expect(body.active.map((l) => l.text).sort()).toEqual(["use X", "use Y"]);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("approveLesson validates a live lesson and 404s an unknown id", () => {
    const dir = root();
    try {
      seedLessons(dir, "grid_ctf", ["use X"]);
      const routes = routesFor(dir);
      const id = (routes.lessonLifecycle("grid_ctf").body as { active: Array<{ id: string }> })
        .active[0]!.id;
      expect(routes.approveLesson("grid_ctf", id)).toEqual({
        status: 200,
        body: { ok: true, status: "active" },
      });
      expect(routes.approveLesson("grid_ctf", "nope").status).toBe(404);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("rejectLesson removes the bullet from the playbook", () => {
    const dir = root();
    try {
      seedLessons(dir, "grid_ctf", ["use X", "use Y"]);
      const routes = routesFor(dir);
      const id = (
        routes.lessonLifecycle("grid_ctf").body as { active: Array<{ id: string; text: string }> }
      ).active.find((l) => l.text === "use X")!.id;
      expect(routes.rejectLesson("grid_ctf", id)).toEqual({ status: 200, body: { ok: true } });
      const after = routes.lessonLifecycle("grid_ctf").body as { active: Array<{ text: string }> };
      expect(after.active.map((l) => l.text)).toEqual(["use Y"]);
      expect(routes.rejectLesson("grid_ctf", id).status).toBe(404);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("curateLesson marks stale / moves to dead-end and rejects an invalid action", () => {
    const dir = root();
    try {
      seedLessons(dir, "grid_ctf", ["use X", "use Y"]);
      const routes = routesFor(dir);
      const lessons = (
        routes.lessonLifecycle("grid_ctf").body as { active: Array<{ id: string; text: string }> }
      ).active;
      const xId = lessons.find((l) => l.text === "use X")!.id;
      const yId = lessons.find((l) => l.text === "use Y")!.id;

      expect(routes.curateLesson("grid_ctf", xId, { action: "stale" })).toEqual({
        status: 200,
        body: { ok: true, status: "stale" },
      });
      expect(routes.curateLesson("grid_ctf", yId, { action: "deadEnd" })).toEqual({
        status: 200,
        body: { ok: true, status: "deadEnd" },
      });

      const lc = routes.lessonLifecycle("grid_ctf").body as {
        active: unknown[];
        stale: Array<{ text: string }>;
        deadEnd: Array<{ text: string }>;
      };
      expect(lc.stale.map((l) => l.text)).toEqual(["use X"]);
      expect(lc.deadEnd.map((l) => l.text)).toEqual(["use Y"]);

      expect(routes.curateLesson("grid_ctf", xId, { action: "bogus" }).status).toBe(422);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });

  it("422s an invalid scenario id", () => {
    const dir = root();
    try {
      expect(routesFor(dir).lessonLifecycle("../escape").status).toBe(422);
    } finally {
      rmSync(dir, { recursive: true, force: true });
    }
  });
});
