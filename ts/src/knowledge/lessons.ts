/** Structured lessons with applicability metadata (Cowork 2c, mirrors Python LessonStore). */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import { resolveScenarioRoot } from "./scenario-paths.js";

const UNSET_GEN = -999_999;

export interface ApplicabilityMeta {
  createdAt: string;
  generation: number;
  bestScore: number;
  schemaVersion: string;
  upstreamSig: string;
  operationType: string;
  supersededBy: string;
  lastValidatedGen: number;
  approvalStatus: string; // "active" (applied) | "pending" (awaiting human approval)
}

export interface Lesson {
  id: string;
  text: string;
  meta: ApplicabilityMeta;
}

export function makeMeta(opts: {
  generation: number;
  bestScore: number;
  createdAt?: string;
  lastValidatedGen?: number;
  operationType?: string;
  approvalStatus?: string;
}): ApplicabilityMeta {
  return {
    createdAt: opts.createdAt ?? "",
    generation: opts.generation,
    bestScore: opts.bestScore,
    schemaVersion: "",
    upstreamSig: "",
    operationType: opts.operationType ?? "advance",
    supersededBy: "",
    lastValidatedGen: opts.lastValidatedGen ?? opts.generation,
    approvalStatus: opts.approvalStatus ?? "active",
  };
}

function metaToDict(m: ApplicabilityMeta): Record<string, unknown> {
  return {
    created_at: m.createdAt,
    generation: m.generation,
    best_score: m.bestScore,
    schema_version: m.schemaVersion,
    upstream_sig: m.upstreamSig,
    operation_type: m.operationType,
    superseded_by: m.supersededBy,
    last_validated_gen: m.lastValidatedGen,
    approval_status: m.approvalStatus,
  };
}

function metaFromDict(d: Record<string, unknown>): ApplicabilityMeta {
  const generation = Number(d.generation ?? 0);
  const lastRaw = d.last_validated_gen;
  return {
    createdAt: String(d.created_at ?? ""),
    generation,
    bestScore: Number(d.best_score ?? 0),
    schemaVersion: String(d.schema_version ?? ""),
    upstreamSig: String(d.upstream_sig ?? ""),
    operationType: String(d.operation_type ?? "advance"),
    supersededBy: String(d.superseded_by ?? ""),
    lastValidatedGen:
      lastRaw === undefined || Number(lastRaw) === UNSET_GEN ? generation : Number(lastRaw),
    approvalStatus: String(d.approval_status ?? "active"),
  };
}

export function lessonToDict(l: Lesson): Record<string, unknown> {
  return { id: l.id, text: l.text, meta: metaToDict(l.meta) };
}

export function lessonFromDict(d: Record<string, unknown>): Lesson {
  return {
    id: String(d.id),
    text: String(d.text),
    meta: metaFromDict((d.meta ?? {}) as Record<string, unknown>),
  };
}

export function isStale(lesson: Lesson, currentGeneration: number, stalenessWindow = 10): boolean {
  if (lesson.meta.lastValidatedGen < 0) return true;
  return currentGeneration - lesson.meta.lastValidatedGen > stalenessWindow;
}

export function isSuperseded(lesson: Lesson): boolean {
  return Boolean(lesson.meta.supersededBy);
}

export function isPending(lesson: Lesson): boolean {
  return lesson.meta.approvalStatus === "pending";
}

export function isApplicable(
  lesson: Lesson,
  currentGeneration: number,
  stalenessWindow = 10,
): boolean {
  // Pending lessons await human approval and must never enter prompts.
  return (
    !isPending(lesson) &&
    !isStale(lesson, currentGeneration, stalenessWindow) &&
    !isSuperseded(lesson)
  );
}

function randomId(): string {
  return `lesson_${randomUUID().replace(/-/g, "").slice(0, 8)}`;
}

export class LessonStore {
  constructor(private readonly knowledgeRoot: string) {}

  private path(scenario: string): string {
    return join(resolveScenarioRoot(this.knowledgeRoot, scenario), "lessons.json");
  }

  readLessons(scenario: string): Lesson[] {
    const p = this.path(scenario);
    if (!existsSync(p)) return [];
    try {
      const data = JSON.parse(readFileSync(p, "utf-8")) as unknown;
      if (!Array.isArray(data)) return [];
      return data.map((e) => lessonFromDict(e as Record<string, unknown>));
    } catch {
      return [];
    }
  }

  writeLessons(scenario: string, lessons: Lesson[]): void {
    const p = this.path(scenario);
    mkdirSync(join(this.knowledgeRoot, scenario), { recursive: true });
    writeFileSync(p, JSON.stringify(lessons.map(lessonToDict), null, 2), "utf-8");
  }

  addLesson(scenario: string, text: string, meta: ApplicabilityMeta): Lesson {
    const lessons = this.readLessons(scenario);
    const lesson: Lesson = { id: randomId(), text, meta };
    lessons.push(lesson);
    this.writeLessons(scenario, lessons);
    return lesson;
  }

  currentGeneration(scenario: string): number {
    const lessons = this.readLessons(scenario);
    if (lessons.length === 0) return 0;
    return Math.max(...lessons.map((l) => Math.max(l.meta.generation, l.meta.lastValidatedGen)));
  }
}
