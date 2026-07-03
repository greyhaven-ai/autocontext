/** Lesson lifecycle derived from live playbook markdown primitives. */
import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import type { ScenarioName } from "../domain/ids.js";
import { ArtifactStore } from "./artifact-store.js";
import { PLAYBOOK_MARKERS } from "./playbook.js";
import { normalizeScenarioSegment } from "./scenario-paths.js";

export const STALENESS_WINDOW = 10;
export const STALE_MARKER = "<!-- autocontext:lesson-status=stale -->";

export interface LessonView {
  id: string;
  text: string;
  status: "pending" | "active" | "stale" | "deadEnd";
  generation: number;
  createdAt: string;
  bestScore: number | null;
  lastValidatedGen: number | null;
  supersededBy: string | null;
  source: string;
}

export interface LifecycleView {
  scenario: string;
  pending: LessonView[];
  active: LessonView[];
  stale: LessonView[];
  deadEnd: LessonView[];
}

type DerivedLesson = {
  id: string;
  text: string;
  status: "active" | "stale";
  source: string;
};

type RewriteMode = "remove" | "stale" | "active";

function normalizeText(text: string): string {
  let stripped = text.replace(STALE_MARKER, "").trim();
  if (stripped.startsWith("- ")) stripped = stripped.slice(2).trim();
  return stripped.split(/\s+/).filter(Boolean).join(" ");
}

function lessonId(text: string): string {
  return "lesson_" + createHash("sha256").update(normalizeText(text)).digest("hex").slice(0, 12);
}

function parseLessonLine(line: string): { text: string; stale: boolean } | null {
  const stripped = line.trim();
  if (!stripped.startsWith("-")) return null;
  const text = normalizeText(stripped.slice(1));
  return text ? { text, stale: stripped.includes(STALE_MARKER) } : null;
}

function playbookBlock(content: string): { start: number; end: number; body: string } | null {
  const startMarker = PLAYBOOK_MARKERS.LESSONS_START;
  const endMarker = PLAYBOOK_MARKERS.LESSONS_END;
  const markerStart = content.indexOf(startMarker);
  const end = content.indexOf(endMarker);
  if (markerStart === -1 || end === -1 || end <= markerStart) return null;
  const start = markerStart + startMarker.length;
  return { start, end, body: content.slice(start, end) };
}

function playbookLessons(artifacts: ArtifactStore, scenario: ScenarioName): DerivedLesson[] {
  const block = playbookBlock(artifacts.readPlaybook(scenario));
  if (!block) return [];
  return block.body
    .split("\n")
    .map(parseLessonLine)
    .filter((item): item is { text: string; stale: boolean } => item !== null)
    .map((item) => ({
      id: lessonId(item.text),
      text: item.text,
      status: item.stale ? "stale" : "active",
      source: `knowledge/${scenario}/playbook.md`,
    }));
}

function skillLessons(skillsRoot: string | undefined, scenario: ScenarioName): DerivedLesson[] {
  if (!skillsRoot) return [];
  const skillPath = join(skillsRoot, `${scenario.replace(/_/g, "-")}-ops`, "SKILL.md");
  if (!existsSync(skillPath)) return [];
  const content = readFileSync(skillPath, "utf-8");
  const marker = "## Operational Lessons";
  const start = content.indexOf(marker);
  if (start === -1) return [];
  const after = content.slice(start + marker.length);
  const nextHeading = after.indexOf("\n## ");
  const section = nextHeading === -1 ? after : after.slice(0, nextHeading);
  return section
    .split("\n")
    .map(parseLessonLine)
    .filter((item): item is { text: string; stale: boolean } => item !== null)
    .map((item) => ({
      id: lessonId(item.text),
      text: item.text,
      status: item.stale ? "stale" : "active",
      source: `skills/${scenario.replace(/_/g, "-")}-ops/SKILL.md`,
    }));
}

function derivedLessons(
  artifacts: ArtifactStore,
  scenario: ScenarioName,
  skillsRoot: string | undefined,
): DerivedLesson[] {
  const seen = new Set<string>();
  const result: DerivedLesson[] = [];
  for (const lesson of [
    ...playbookLessons(artifacts, scenario),
    ...skillLessons(skillsRoot, scenario),
  ]) {
    if (seen.has(lesson.id)) continue;
    seen.add(lesson.id);
    result.push(lesson);
  }
  return result;
}

function lessonView(lesson: DerivedLesson, currentGeneration: number): LessonView {
  return {
    id: lesson.id,
    text: lesson.text,
    status: lesson.status,
    generation: 0,
    createdAt: "",
    bestScore: null,
    lastValidatedGen: lesson.status === "active" ? currentGeneration : null,
    supersededBy: null,
    source: lesson.source,
  };
}

function deadEndViews(md: string): LessonView[] {
  return md
    .split("### Dead End")
    .map((block) => block.trim())
    .filter((block) => block.length > 0)
    .map((text) => ({
      id: "deadend_" + createHash("sha256").update(text).digest("hex").slice(0, 8),
      text,
      status: "deadEnd" as const,
      generation: 0,
      createdAt: "",
      bestScore: null,
      lastValidatedGen: null,
      supersededBy: null,
      source: "knowledge/dead_ends.md",
    }));
}

export function buildLifecycle(opts: {
  knowledgeRoot: string;
  scenario: string;
  currentGeneration: number;
  stalenessWindow?: number;
  skillsRoot?: string;
}): LifecycleView {
  void opts.stalenessWindow;
  const scenario = normalizeScenarioSegment(opts.scenario);
  const artifacts = new ArtifactStore({
    runsRoot: opts.knowledgeRoot,
    knowledgeRoot: opts.knowledgeRoot,
  });
  const active: LessonView[] = [];
  const stale: LessonView[] = [];
  for (const lesson of derivedLessons(artifacts, scenario, opts.skillsRoot)) {
    (lesson.status === "stale" ? stale : active).push(lessonView(lesson, opts.currentGeneration));
  }
  return {
    scenario,
    pending: [],
    active,
    stale,
    deadEnd: deadEndViews(artifacts.readDeadEnds(scenario)),
  };
}

export function approveLesson(opts: {
  knowledgeRoot: string;
  skillsRoot?: string;
  scenario: string;
  lessonId: string;
  currentGeneration: number;
}): string | null {
  void opts.currentGeneration;
  const result = setLessonStale(
    opts.knowledgeRoot,
    opts.skillsRoot,
    opts.scenario,
    opts.lessonId,
    false,
  );
  return result.found ? "active" : null;
}

export function rejectLesson(opts: {
  knowledgeRoot: string;
  skillsRoot?: string;
  scenario: string;
  lessonId: string;
}): boolean {
  return removeLesson(opts.knowledgeRoot, opts.skillsRoot, opts.scenario, opts.lessonId).found;
}

export function curateLesson(opts: {
  knowledgeRoot: string;
  skillsRoot?: string;
  scenario: string;
  lessonId: string;
  action: "stale" | "deadEnd" | "delete";
  currentGeneration: number;
}): string | null {
  void opts.currentGeneration;
  if (opts.action === "delete") {
    return removeLesson(opts.knowledgeRoot, opts.skillsRoot, opts.scenario, opts.lessonId).found
      ? "deleted"
      : null;
  }
  if (opts.action === "stale") {
    return setLessonStale(opts.knowledgeRoot, opts.skillsRoot, opts.scenario, opts.lessonId, true)
      .found
      ? "stale"
      : null;
  }
  const result = removeLesson(opts.knowledgeRoot, opts.skillsRoot, opts.scenario, opts.lessonId);
  if (!result.found || !result.text) return null;
  new ArtifactStore({
    runsRoot: opts.knowledgeRoot,
    knowledgeRoot: opts.knowledgeRoot,
  }).appendDeadEnd(normalizeScenarioSegment(opts.scenario), result.text);
  return "deadEnd";
}

function removeLesson(
  knowledgeRoot: string,
  skillsRoot: string | undefined,
  scenarioName: string,
  id: string,
): { found: boolean; text: string | null } {
  const playbook = rewritePlaybookLesson(knowledgeRoot, scenarioName, id, "remove");
  const skill = rewriteSkillLesson(skillsRoot, scenarioName, id, "remove");
  return { found: playbook.found || skill.found, text: playbook.text ?? skill.text };
}

function setLessonStale(
  knowledgeRoot: string,
  skillsRoot: string | undefined,
  scenarioName: string,
  id: string,
  stale: boolean,
): { found: boolean; text: string | null } {
  const mode = stale ? "stale" : "active";
  const playbook = rewritePlaybookLesson(knowledgeRoot, scenarioName, id, mode);
  const skill = rewriteSkillLesson(skillsRoot, scenarioName, id, mode);
  return { found: playbook.found || skill.found, text: playbook.text ?? skill.text };
}

function rewritePlaybookLesson(
  knowledgeRoot: string,
  scenarioName: string,
  id: string,
  mode: RewriteMode,
): { found: boolean; text: string | null } {
  const scenario = normalizeScenarioSegment(scenarioName);
  const artifacts = new ArtifactStore({ runsRoot: knowledgeRoot, knowledgeRoot });
  const content = artifacts.readPlaybook(scenario);
  const block = playbookBlock(content);
  if (!block) return { found: false, text: null };
  const rewritten = rewriteLessonLines(block.body, id, mode);
  if (!rewritten.found) return rewritten;
  if (!rewritten.changed) return { found: true, text: rewritten.text };
  const body = rewritten.lines.join("\n").trim();
  const prefix = content.slice(0, block.start).trimEnd();
  const suffix = content.slice(block.end).trimStart();
  artifacts.writePlaybook(
    scenario,
    body ? `${prefix}\n${body}\n${suffix}` : `${prefix}\n${suffix}`,
  );
  return { found: true, text: rewritten.text };
}

function rewriteSkillLesson(
  skillsRoot: string | undefined,
  scenarioName: string,
  id: string,
  mode: RewriteMode,
): { found: boolean; text: string | null } {
  if (!skillsRoot) return { found: false, text: null };
  const scenario = normalizeScenarioSegment(scenarioName);
  const path = join(skillsRoot, `${scenario.replace(/_/g, "-")}-ops`, "SKILL.md");
  if (!existsSync(path)) return { found: false, text: null };
  const content = readFileSync(path, "utf-8");
  const marker = "## Operational Lessons";
  const markerStart = content.indexOf(marker);
  if (markerStart === -1) return { found: false, text: null };
  const start = markerStart + marker.length;
  const after = content.slice(start);
  const nextHeading = after.indexOf("\n## ");
  const end = nextHeading === -1 ? content.length : start + nextHeading;
  const rewritten = rewriteLessonLines(content.slice(start, end), id, mode);
  if (!rewritten.found) return rewritten;
  if (rewritten.changed) {
    const body = rewritten.lines.join("\n").trim();
    const prefix = content.slice(0, start).trimEnd();
    const suffix = content.slice(end).trimStart();
    writeFileSync(path, body ? `${prefix}\n${body}\n${suffix}` : `${prefix}\n${suffix}`, "utf-8");
  }
  return { found: true, text: rewritten.text };
}

function rewriteLessonLines(
  body: string,
  id: string,
  mode: RewriteMode,
): { found: boolean; changed: boolean; text: string | null; lines: string[] } {
  let found = false;
  let changed = false;
  let targetText: string | null = null;
  const lines: string[] = [];
  for (const line of body.split("\n")) {
    const parsed = parseLessonLine(line);
    if (!parsed || lessonId(parsed.text) !== id) {
      lines.push(line);
      continue;
    }
    found = true;
    targetText ??= parsed.text;
    if (mode === "remove") {
      changed = true;
      continue;
    }
    if (mode === "active" && !parsed.stale) {
      lines.push(line);
      continue;
    }
    if (mode === "stale" && parsed.stale) {
      lines.push(line);
      continue;
    }
    changed = true;
    lines.push(`- ${parsed.text}${mode === "stale" ? ` ${STALE_MARKER}` : ""}`);
  }
  return { found, changed, text: targetText, lines };
}

export { LessonStore, makeMeta } from "./lessons.js";
