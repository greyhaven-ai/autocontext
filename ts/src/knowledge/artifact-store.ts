/**
 * Artifact store — file-based persistence for runs, knowledge, tools (AC-344 Task 10b).
 * Mirrors the core subset of Python's autocontext/storage/artifacts.py.
 */

import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import { PlaybookManager, EMPTY_PLAYBOOK_SENTINEL } from "./playbook.js";

export interface ArtifactStoreOpts {
  runsRoot: string;
  knowledgeRoot: string;
  maxPlaybookVersions?: number;
}

export class ArtifactStore {
  readonly runsRoot: string;
  readonly knowledgeRoot: string;
  private playbookManager: PlaybookManager;

  constructor(opts: ArtifactStoreOpts) {
    this.runsRoot = opts.runsRoot;
    this.knowledgeRoot = opts.knowledgeRoot;
    this.playbookManager = new PlaybookManager(
      opts.knowledgeRoot,
      opts.maxPlaybookVersions ?? 5,
    );
  }

  generationDir(runId: string, generationIndex: number): string {
    return join(this.runsRoot, runId, "generations", `gen_${generationIndex}`);
  }

  writeJson(path: string, payload: Record<string, unknown>): void {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, JSON.stringify(payload, null, 2) + "\n", "utf-8");
  }

  writeMarkdown(path: string, content: string): void {
    mkdirSync(dirname(path), { recursive: true });
    writeFileSync(path, content.trim() + "\n", "utf-8");
  }

  appendMarkdown(path: string, content: string, heading: string): void {
    mkdirSync(dirname(path), { recursive: true });
    const chunk = `\n## ${heading}\n\n${content.trim()}\n`;
    if (existsSync(path)) {
      appendFileSync(path, chunk, "utf-8");
    } else {
      writeFileSync(path, chunk.replace(/^\n/, ""), "utf-8");
    }
  }

  readPlaybook(scenarioName: string): string {
    return this.playbookManager.read(scenarioName);
  }

  writePlaybook(scenarioName: string, content: string): void {
    this.playbookManager.write(scenarioName, content);
  }
}

export { EMPTY_PLAYBOOK_SENTINEL };
