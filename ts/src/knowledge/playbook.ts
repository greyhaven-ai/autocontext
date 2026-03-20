/**
 * Playbook manager with versioning and integrity guard (AC-344 Task 10).
 * Mirrors Python's autocontext/storage/artifacts.py (playbook methods)
 * and autocontext/knowledge/playbook_guard.py.
 */

import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { VersionedFileStore } from "./versioned-store.js";

export const EMPTY_PLAYBOOK_SENTINEL =
  "No playbook yet. Start from scenario rules and observation.";

export const PLAYBOOK_MARKERS = {
  PLAYBOOK_START: "<!-- PLAYBOOK_START -->",
  PLAYBOOK_END: "<!-- PLAYBOOK_END -->",
  LESSONS_START: "<!-- LESSONS_START -->",
  LESSONS_END: "<!-- LESSONS_END -->",
  HINTS_START: "<!-- COMPETITOR_HINTS_START -->",
  HINTS_END: "<!-- COMPETITOR_HINTS_END -->",
} as const;

export class PlaybookManager {
  private knowledgeRoot: string;
  private maxVersions: number;
  private stores = new Map<string, VersionedFileStore>();

  constructor(knowledgeRoot: string, maxVersions = 5) {
    this.knowledgeRoot = knowledgeRoot;
    this.maxVersions = maxVersions;
  }

  private store(scenarioName: string): VersionedFileStore {
    let s = this.stores.get(scenarioName);
    if (!s) {
      s = new VersionedFileStore(join(this.knowledgeRoot, scenarioName), {
        maxVersions: this.maxVersions,
        versionsDirName: "playbook_versions",
        versionPrefix: "playbook_v",
        versionSuffix: ".md",
      });
      this.stores.set(scenarioName, s);
    }
    return s;
  }

  read(scenarioName: string): string {
    const content = this.store(scenarioName).read("playbook.md");
    return content || EMPTY_PLAYBOOK_SENTINEL;
  }

  write(scenarioName: string, content: string): void {
    mkdirSync(join(this.knowledgeRoot, scenarioName), { recursive: true });
    this.store(scenarioName).write("playbook.md", content.trim() + "\n");
  }

  rollback(scenarioName: string): boolean {
    return this.store(scenarioName).rollback("playbook.md");
  }

  versionCount(scenarioName: string): number {
    return this.store(scenarioName).versionCount("playbook.md");
  }
}

// ---------------------------------------------------------------------------
// PlaybookGuard
// ---------------------------------------------------------------------------

export interface GuardResult {
  approved: boolean;
  reason: string;
}

export class PlaybookGuard {
  private maxShrink: number;

  static REQUIRED_MARKERS: Array<[string, string]> = [
    [PLAYBOOK_MARKERS.PLAYBOOK_START, PLAYBOOK_MARKERS.PLAYBOOK_END],
    [PLAYBOOK_MARKERS.LESSONS_START, PLAYBOOK_MARKERS.LESSONS_END],
    [PLAYBOOK_MARKERS.HINTS_START, PLAYBOOK_MARKERS.HINTS_END],
  ];

  constructor(maxShrinkRatio = 0.3) {
    this.maxShrink = maxShrinkRatio;
  }

  check(current: string, proposed: string): GuardResult {
    if (current) {
      if (!proposed) {
        return { approved: false, reason: "Proposed playbook is empty (100% shrinkage)" };
      }
      const ratio = proposed.length / current.length;
      if (ratio < this.maxShrink) {
        return {
          approved: false,
          reason: `Playbook shrink ratio ${ratio.toFixed(2)} below threshold ${this.maxShrink}`,
        };
      }
    }

    for (const [start, end] of PlaybookGuard.REQUIRED_MARKERS) {
      if (current.includes(start) && !proposed.includes(start)) {
        return { approved: false, reason: `Required marker '${start}' missing from proposed playbook` };
      }
      if (current.includes(end) && !proposed.includes(end)) {
        return { approved: false, reason: `Required marker '${end}' missing from proposed playbook` };
      }
    }

    return { approved: true, reason: "" };
  }
}
