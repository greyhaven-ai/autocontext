import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { ArtifactStore } from "../src/knowledge/artifact-store.js";
import { asScenarioName } from "../src/domain/ids.js";

let dir: string;
beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "ac-lifecycle-"));
});
afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
});

async function mods() {
  return {
    ...(await import("../src/knowledge/lesson-lifecycle.js")),
    ...(await import("../src/knowledge/lessons.js")),
  };
}

function store(): ArtifactStore {
  return new ArtifactStore({ runsRoot: dir, knowledgeRoot: dir });
}

function playbook(...lessons: string[]): string {
  return [
    "intro",
    "<!-- LESSONS_START -->",
    ...lessons.map((lesson) => `- ${lesson}`),
    "<!-- LESSONS_END -->",
    "outro",
  ].join("\n");
}

describe("lesson lifecycle (derived markdown view)", () => {
  it("buildLifecycle derives active lessons from playbook/SKILL and reads dead ends", async () => {
    const { buildLifecycle } = await mods();
    const artifacts = store();
    artifacts.writePlaybook(asScenarioName("scn"), playbook("fresh", "shared"));
    const skillDir = join(dir, "skills", "scn-ops");
    mkdirSync(skillDir, { recursive: true });
    writeFileSync(
      join(skillDir, "SKILL.md"),
      "# Skill\n\n## Operational Lessons\n\n- shared\n- skill only\n",
      "utf-8",
    );
    artifacts.appendDeadEnd(asScenarioName("scn"), "tried Y, lost");

    const view = buildLifecycle({
      knowledgeRoot: dir,
      skillsRoot: join(dir, "skills"),
      scenario: "scn",
      currentGeneration: 20,
    });

    expect(view.active.map((l: { text: string }) => l.text)).toEqual([
      "fresh",
      "shared",
      "skill only",
    ]);
    expect(view.pending).toEqual([]);
    expect(view.stale).toEqual([]);
    expect(view.deadEnd[0].text).toContain("tried Y");
    expect(view.active[0].id).toMatch(/^lesson_/);
    expect(view.active[0].source).toMatch(/playbook\.md$/);
  });

  it("curates skill-only lessons listed in the derived view", async () => {
    const { buildLifecycle, curateLesson, rejectLesson } = await mods();
    const skillDir = join(dir, "skills", "scn-ops");
    const skillPath = join(skillDir, "SKILL.md");
    mkdirSync(skillDir, { recursive: true });
    writeFileSync(skillPath, "# Skill\n\n## Operational Lessons\n\n- skill only\n", "utf-8");
    const lesson = buildLifecycle({
      knowledgeRoot: dir,
      skillsRoot: join(dir, "skills"),
      scenario: "scn",
      currentGeneration: 1,
    }).active.find((item: { text: string }) => item.text === "skill only")!;

    expect(
      curateLesson({
        knowledgeRoot: dir,
        skillsRoot: join(dir, "skills"),
        scenario: "scn",
        lessonId: lesson.id,
        action: "stale",
        currentGeneration: 1,
      }),
    ).toBe("stale");
    expect(readFileSync(skillPath, "utf-8")).toContain("autocontext:lesson-status=stale");
    expect(
      buildLifecycle({
        knowledgeRoot: dir,
        skillsRoot: join(dir, "skills"),
        scenario: "scn",
        currentGeneration: 1,
      }).stale.map((item: { text: string }) => item.text),
    ).toEqual(["skill only"]);

    expect(
      curateLesson({
        knowledgeRoot: dir,
        skillsRoot: join(dir, "skills"),
        scenario: "scn",
        lessonId: lesson.id,
        action: "deadEnd",
        currentGeneration: 1,
      }),
    ).toBe("deadEnd");
    expect(readFileSync(skillPath, "utf-8")).not.toContain("skill only");
    expect(store().readDeadEnds(asScenarioName("scn"))).toContain("skill only");

    writeFileSync(skillPath, "# Skill\n\n## Operational Lessons\n\n- reject me\n", "utf-8");
    const rejectId = buildLifecycle({
      knowledgeRoot: dir,
      skillsRoot: join(dir, "skills"),
      scenario: "scn",
      currentGeneration: 1,
    }).active[0].id;
    expect(
      rejectLesson({
        knowledgeRoot: dir,
        skillsRoot: join(dir, "skills"),
        scenario: "scn",
        lessonId: rejectId,
      }),
    ).toBe(true);
    expect(readFileSync(skillPath, "utf-8")).not.toContain("reject me");
  });

  it("approve is a no-op for a live derived lesson", async () => {
    const { approveLesson, buildLifecycle } = await mods();
    const artifacts = store();
    artifacts.writePlaybook(asScenarioName("scn"), playbook("already live"));
    const [lesson] = buildLifecycle({
      knowledgeRoot: dir,
      scenario: "scn",
      currentGeneration: 1,
    }).active;

    expect(
      approveLesson({
        knowledgeRoot: dir,
        scenario: "scn",
        lessonId: lesson.id,
        currentGeneration: 0,
      }),
    ).toBe("active");
    expect(
      approveLesson({
        knowledgeRoot: dir,
        scenario: "scn",
        lessonId: "missing",
        currentGeneration: 9,
      }),
    ).toBeNull();
  });

  it("reject removes a live markdown lesson", async () => {
    const { rejectLesson, buildLifecycle } = await mods();
    const artifacts = store();
    artifacts.writePlaybook(asScenarioName("scn"), playbook("held", "keep"));
    const lesson = buildLifecycle({
      knowledgeRoot: dir,
      scenario: "scn",
      currentGeneration: 1,
    }).active.find((l: { text: string }) => l.text === "held")!;

    expect(rejectLesson({ knowledgeRoot: dir, scenario: "scn", lessonId: lesson.id })).toBe(true);
    expect(artifacts.readPlaybook(asScenarioName("scn"))).not.toContain("held");
    expect(artifacts.readPlaybook(asScenarioName("scn"))).toContain("keep");
  });

  it("curate deadEnd moves the markdown lesson into the shared registry", async () => {
    const { curateLesson, buildLifecycle } = await mods();
    const artifacts = store();
    artifacts.writePlaybook(asScenarioName("scn"), playbook("dead me"));
    const [lesson] = buildLifecycle({
      knowledgeRoot: dir,
      scenario: "scn",
      currentGeneration: 9,
    }).active;

    expect(
      curateLesson({
        knowledgeRoot: dir,
        scenario: "scn",
        lessonId: lesson.id,
        action: "deadEnd",
        currentGeneration: 9,
      }),
    ).toBe("deadEnd");
    expect(artifacts.readPlaybook(asScenarioName("scn"))).not.toContain("dead me");
    expect(
      buildLifecycle({ knowledgeRoot: dir, scenario: "scn", currentGeneration: 9 }).deadEnd[0].text,
    ).toContain("dead me");
  });

  it("curate stale annotates without deleting and excludes stale from active", async () => {
    const { curateLesson, buildLifecycle } = await mods();
    const artifacts = store();
    artifacts.writePlaybook(asScenarioName("scn"), playbook("stale me"));
    const [lesson] = buildLifecycle({
      knowledgeRoot: dir,
      scenario: "scn",
      currentGeneration: 9,
    }).active;

    expect(
      curateLesson({
        knowledgeRoot: dir,
        scenario: "scn",
        lessonId: lesson.id,
        action: "stale",
        currentGeneration: 9,
      }),
    ).toBe("stale");
    const view = buildLifecycle({ knowledgeRoot: dir, scenario: "scn", currentGeneration: 9 });
    expect(view.active).toEqual([]);
    expect(view.stale.map((l: { text: string }) => l.text)).toEqual(["stale me"]);
    expect(artifacts.readPlaybook(asScenarioName("scn"))).toContain("stale me");
  });

  it("deletes and returns null on missing", async () => {
    const { curateLesson, buildLifecycle } = await mods();
    const artifacts = store();
    artifacts.writePlaybook(asScenarioName("scn"), playbook("gone"));
    const [lesson] = buildLifecycle({
      knowledgeRoot: dir,
      scenario: "scn",
      currentGeneration: 9,
    }).active;

    expect(
      curateLesson({
        knowledgeRoot: dir,
        scenario: "scn",
        lessonId: lesson.id,
        action: "delete",
        currentGeneration: 9,
      }),
    ).toBe("deleted");
    expect(artifacts.readPlaybook(asScenarioName("scn"))).not.toContain("gone");
    expect(
      curateLesson({
        knowledgeRoot: dir,
        scenario: "scn",
        lessonId: "nope",
        action: "delete",
        currentGeneration: 9,
      }),
    ).toBeNull();
  });

  it("structured LessonStore no longer feeds lifecycle or prompt paths", async () => {
    const { buildLifecycle, LessonStore, makeMeta } = await mods();
    const lessonStore = new LessonStore(dir);
    lessonStore.writeLessons("scn", [
      { id: "x", text: "structured shadow", meta: makeMeta({ generation: 1, bestScore: 0 }) },
    ]);

    expect(
      buildLifecycle({ knowledgeRoot: dir, scenario: "scn", currentGeneration: 1 }).active,
    ).toEqual([]);
  });

  it("rejects path-traversal scenario names and writes nothing outside the root", async () => {
    const { LessonStore, makeMeta } = await mods();
    const lessonStore = new LessonStore(join(dir, "knowledge"));
    expect(() =>
      lessonStore.writeLessons("../outside", [
        { id: "x", text: "escape", meta: makeMeta({ generation: 1, bestScore: 0 }) },
      ]),
    ).toThrow();
    expect(existsSync(join(dir, "outside"))).toBe(false);
  });
});
