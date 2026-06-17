/** Pure lesson-lifecycle assembly + curation ops (Cowork 2c, mirrors Python).
 *
 * Backed by the structured LessonStore (lessons.json) and the ArtifactStore
 * dead_ends.md registry. "pending" is a status on the lesson itself
 * (meta.approvalStatus), not a separate store, so the lifecycle reads one store.
 */
import { createHash } from "node:crypto";
import { type Lesson, LessonStore, isPending, isStale, isSuperseded } from "./lessons.js";
import { ArtifactStore } from "./artifact-store.js";
import { normalizeScenarioSegment } from "./scenario-paths.js";

export const STALENESS_WINDOW = 10;

export interface LessonView {
  id: string;
  text: string;
  status: "pending" | "active" | "stale" | "deadEnd";
  generation: number;
  createdAt: string;
  bestScore: number | null;
  lastValidatedGen: number | null;
  supersededBy: string | null;
  source: "curator" | "human";
}

function lessonView(l: Lesson, status: LessonView["status"]): LessonView {
  return {
    id: l.id,
    text: l.text,
    status,
    generation: l.meta.generation,
    createdAt: l.meta.createdAt,
    bestScore: l.meta.bestScore,
    lastValidatedGen: l.meta.lastValidatedGen,
    supersededBy: l.meta.supersededBy || null,
    source: "curator",
  };
}

function deadEndStore(knowledgeRoot: string): ArtifactStore {
  // Dead ends only need knowledgeRoot; runsRoot is unused for read/append.
  return new ArtifactStore({ runsRoot: knowledgeRoot, knowledgeRoot });
}

function deadEndViews(md: string): LessonView[] {
  // ArtifactStore.appendDeadEnd writes "### Dead End\n\n{entry}\n" sections.
  return md
    .split("### Dead End")
    .map((block) => block.trim())
    .filter((block) => block.length > 0)
    .map((text) => ({
      id: "deadend_" + createHash("sha1").update(text).digest("hex").slice(0, 8),
      text,
      status: "deadEnd" as const,
      generation: 0,
      createdAt: "",
      bestScore: null,
      lastValidatedGen: null,
      supersededBy: null,
      source: "curator" as const,
    }));
}

export interface LifecycleView {
  scenario: string;
  pending: LessonView[];
  active: LessonView[];
  stale: LessonView[];
  deadEnd: LessonView[];
}

export function buildLifecycle(opts: {
  knowledgeRoot: string;
  scenario: string;
  currentGeneration: number;
  stalenessWindow?: number;
}): LifecycleView {
  const scenario = normalizeScenarioSegment(opts.scenario);
  const window = opts.stalenessWindow ?? STALENESS_WINDOW;
  const store = new LessonStore(opts.knowledgeRoot);
  const pending: LessonView[] = [];
  const active: LessonView[] = [];
  const stale: LessonView[] = [];
  for (const l of store.readLessons(scenario)) {
    if (isPending(l)) {
      pending.push(lessonView(l, "pending"));
      continue;
    }
    if (isSuperseded(l)) continue;
    if (isStale(l, opts.currentGeneration, window)) stale.push(lessonView(l, "stale"));
    else active.push(lessonView(l, "active"));
  }
  return {
    scenario,
    pending,
    active,
    stale,
    deadEnd: deadEndViews(deadEndStore(opts.knowledgeRoot).readDeadEnds(scenario)),
  };
}

export function approveLesson(opts: {
  knowledgeRoot: string;
  scenario: string;
  lessonId: string;
  currentGeneration: number;
}): string | null {
  const scenario = normalizeScenarioSegment(opts.scenario);
  const store = new LessonStore(opts.knowledgeRoot);
  const lessons = store.readLessons(scenario);
  const target = lessons.find((l) => l.id === opts.lessonId && isPending(l));
  if (!target) return null;
  target.meta.approvalStatus = "active";
  // Never lower the validation generation: approving must not make a lesson stale.
  target.meta.lastValidatedGen = Math.max(
    opts.currentGeneration,
    target.meta.generation,
    target.meta.lastValidatedGen,
  );
  store.writeLessons(scenario, lessons);
  return "active";
}

export function rejectLesson(opts: {
  knowledgeRoot: string;
  scenario: string;
  lessonId: string;
}): boolean {
  // Reject only removes pending lessons; deleting an active lesson is the explicit
  // curate "delete" action.
  const scenario = normalizeScenarioSegment(opts.scenario);
  const store = new LessonStore(opts.knowledgeRoot);
  const lessons = store.readLessons(scenario);
  const target = lessons.find((l) => l.id === opts.lessonId && isPending(l));
  if (!target) return false;
  store.writeLessons(
    scenario,
    lessons.filter((l) => l.id !== opts.lessonId),
  );
  return true;
}

export function curateLesson(opts: {
  knowledgeRoot: string;
  scenario: string;
  lessonId: string;
  action: "stale" | "deadEnd" | "delete";
  currentGeneration: number;
}): string | null {
  const scenario = normalizeScenarioSegment(opts.scenario);
  const store = new LessonStore(opts.knowledgeRoot);
  const lessons = store.readLessons(scenario);
  const target = lessons.find((l) => l.id === opts.lessonId);
  if (!target) return null;
  if (opts.action === "delete") {
    store.writeLessons(
      scenario,
      lessons.filter((l) => l.id !== opts.lessonId),
    );
    return "deleted";
  }
  if (opts.action === "stale") {
    target.meta.lastValidatedGen = -1;
    store.writeLessons(scenario, lessons);
    return "stale";
  }
  // deadEnd: append via the shared ArtifactStore registry (single format), remove from active.
  deadEndStore(opts.knowledgeRoot).appendDeadEnd(scenario, target.text);
  store.writeLessons(
    scenario,
    lessons.filter((l) => l.id !== opts.lessonId),
  );
  return "deadEnd";
}

export { LessonStore, makeMeta } from "./lessons.js";
