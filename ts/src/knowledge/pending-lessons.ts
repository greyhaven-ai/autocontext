/** Per-scenario queue of lessons awaiting human approval (Cowork 2c). */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { type Lesson, lessonFromDict, lessonToDict } from "./lessons.js";

export class PendingLessonStore {
  constructor(private readonly knowledgeRoot: string) {}

  private path(scenario: string): string {
    return join(this.knowledgeRoot, scenario, "pending_lessons.json");
  }

  read(scenario: string): Lesson[] {
    const p = this.path(scenario);
    if (!existsSync(p)) return [];
    try {
      const data = JSON.parse(readFileSync(p, "utf-8"));
      if (!Array.isArray(data)) return [];
      return data.map((e) => lessonFromDict(e as Record<string, unknown>));
    } catch {
      return [];
    }
  }

  write(scenario: string, lessons: Lesson[]): void {
    mkdirSync(join(this.knowledgeRoot, scenario), { recursive: true });
    writeFileSync(this.path(scenario), JSON.stringify(lessons.map(lessonToDict), null, 2), "utf-8");
  }

  add(scenario: string, lesson: Lesson): void {
    const lessons = this.read(scenario);
    if (lessons.some((e) => e.id === lesson.id || e.text === lesson.text)) return;
    lessons.push(lesson);
    this.write(scenario, lessons);
  }

  remove(scenario: string, lessonId: string): Lesson | null {
    const lessons = this.read(scenario);
    let removed: Lesson | null = null;
    const kept: Lesson[] = [];
    for (const l of lessons) {
      if (l.id === lessonId && removed === null) removed = l;
      else kept.push(l);
    }
    if (removed !== null) this.write(scenario, kept);
    return removed;
  }
}
