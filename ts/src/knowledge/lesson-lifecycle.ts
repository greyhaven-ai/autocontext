/** Pure lesson-lifecycle assembly + curation ops (Cowork 2c, mirrors Python).
 *
 * Backed by the structured LessonStore (lessons.json) and the dead_ends.md
 * registry. "pending" is a status on the lesson itself (meta.approvalStatus),
 * not a separate store, so the whole lifecycle reads one store.
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { type Lesson, LessonStore, isPending, isStale, isSuperseded } from "./lessons.js";

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

function deadEndsPath(knowledgeRoot: string, scenario: string): string {
  return join(knowledgeRoot, scenario, "dead_ends.md");
}

function readDeadEnds(knowledgeRoot: string, scenario: string): string {
  const p = deadEndsPath(knowledgeRoot, scenario);
  return existsSync(p) ? readFileSync(p, "utf-8") : "";
}

function deadEndViews(md: string): LessonView[] {
  // TS dead-end registry: one "- **Gen N**: ..." bullet per entry.
  return md
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.startsWith("- **Gen"))
    .map((line) => ({
      id: "deadend_" + createHash("sha1").update(line).digest("hex").slice(0, 8),
      text: line.replace(/^-\s*/, ""),
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
  const window = opts.stalenessWindow ?? STALENESS_WINDOW;
  const store = new LessonStore(opts.knowledgeRoot);
  const pending: LessonView[] = [];
  const active: LessonView[] = [];
  const stale: LessonView[] = [];
  for (const l of store.readLessons(opts.scenario)) {
    if (isPending(l)) {
      pending.push(lessonView(l, "pending"));
      continue;
    }
    if (isSuperseded(l)) continue;
    if (isStale(l, opts.currentGeneration, window)) stale.push(lessonView(l, "stale"));
    else active.push(lessonView(l, "active"));
  }
  return {
    scenario: opts.scenario,
    pending,
    active,
    stale,
    deadEnd: deadEndViews(readDeadEnds(opts.knowledgeRoot, opts.scenario)),
  };
}

export function approveLesson(opts: {
  knowledgeRoot: string;
  scenario: string;
  lessonId: string;
  currentGeneration: number;
}): string | null {
  const store = new LessonStore(opts.knowledgeRoot);
  const lessons = store.readLessons(opts.scenario);
  const target = lessons.find((l) => l.id === opts.lessonId && isPending(l));
  if (!target) return null;
  target.meta.approvalStatus = "active";
  // Never lower the validation generation: approving must not make a lesson stale.
  target.meta.lastValidatedGen = Math.max(
    opts.currentGeneration,
    target.meta.generation,
    target.meta.lastValidatedGen,
  );
  store.writeLessons(opts.scenario, lessons);
  return "active";
}

export function rejectLesson(opts: {
  knowledgeRoot: string;
  scenario: string;
  lessonId: string;
}): boolean {
  // Reject only removes pending lessons; deleting an active lesson is the explicit
  // curate "delete" action.
  const store = new LessonStore(opts.knowledgeRoot);
  const lessons = store.readLessons(opts.scenario);
  const target = lessons.find((l) => l.id === opts.lessonId && isPending(l));
  if (!target) return false;
  store.writeLessons(
    opts.scenario,
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
  const store = new LessonStore(opts.knowledgeRoot);
  const lessons = store.readLessons(opts.scenario);
  const target = lessons.find((l) => l.id === opts.lessonId);
  if (!target) return null;
  if (opts.action === "delete") {
    store.writeLessons(
      opts.scenario,
      lessons.filter((l) => l.id !== opts.lessonId),
    );
    return "deleted";
  }
  if (opts.action === "stale") {
    target.meta.lastValidatedGen = -1;
    store.writeLessons(opts.scenario, lessons);
    return "stale";
  }
  // deadEnd: append a bullet to dead_ends.md, remove from active.
  const p = deadEndsPath(opts.knowledgeRoot, opts.scenario);
  mkdirSync(join(opts.knowledgeRoot, opts.scenario), { recursive: true });
  const prev = existsSync(p) ? readFileSync(p, "utf-8") : "# Dead-End Registry\n\n";
  writeFileSync(
    p,
    `${prev.trimEnd()}\n- **Gen ${target.meta.generation}**: ${target.text}\n`,
    "utf-8",
  );
  store.writeLessons(
    opts.scenario,
    lessons.filter((l) => l.id !== opts.lessonId),
  );
  return "deadEnd";
}

export { LessonStore, makeMeta } from "./lessons.js";
