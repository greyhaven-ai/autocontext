/**
 * Agent task CRUD store — file-based task spec persistence (AC-370).
 * Mirrors Python's agent task creation/listing/deletion.
 */

import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { z } from "zod";

export interface AgentTaskSpec {
  name: string;
  taskPrompt: string;
  rubric: string;
  referenceContext?: string;
  requiredConcepts?: string[];
}

const AgentTaskStoreSpecSchema = z.object({
  name: z.string().min(1),
  taskPrompt: z.string().min(1),
  rubric: z.string().min(1),
  referenceContext: z.string().optional(),
  requiredConcepts: z.array(z.string()).optional(),
});

function readTaskSpec(path: string): AgentTaskSpec | null {
  try {
    return AgentTaskStoreSpecSchema.parse(JSON.parse(readFileSync(path, "utf-8")));
  } catch {
    return null;
  }
}

export class AgentTaskStore {
  #dir: string;

  constructor(dir: string) {
    this.#dir = dir;
    mkdirSync(dir, { recursive: true });
  }

  create(spec: AgentTaskSpec): void {
    const parsed = AgentTaskStoreSpecSchema.parse(spec);
    const path = join(this.#dir, `${spec.name}.json`);
    writeFileSync(path, JSON.stringify(parsed, null, 2), "utf-8");
  }

  list(): AgentTaskSpec[] {
    if (!existsSync(this.#dir)) return [];
    return readdirSync(this.#dir)
      .filter((f) => f.endsWith(".json"))
      .map((f) => readTaskSpec(join(this.#dir, f)))
      .filter((s): s is AgentTaskSpec => s !== null);
  }

  get(name: string): AgentTaskSpec | null {
    const path = join(this.#dir, `${name}.json`);
    if (!existsSync(path)) return null;
    return readTaskSpec(path);
  }

  delete(name: string): boolean {
    const path = join(this.#dir, `${name}.json`);
    if (!existsSync(path)) return false;
    rmSync(path);
    return true;
  }
}
