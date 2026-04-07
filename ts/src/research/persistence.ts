/**
 * Research evidence persistence — JSON-file store (AC-500 TS parity).
 */

import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync, unlinkSync } from "node:fs";
import { join } from "node:path";
import { ResearchBrief } from "./consultation.js";

const BRIEFS_DIR = "research_briefs";
const MANIFEST_FILE = "manifest.json";

export interface BriefRef {
  readonly briefId: string;
  readonly sessionId: string;
  readonly goal: string;
  readonly createdAt: string;
  readonly findingCount: number;
}

export class ResearchStore {
  private dir: string;
  private manifestPath: string;
  private manifest: BriefRef[];

  constructor(root: string) {
    this.dir = join(root, BRIEFS_DIR);
    mkdirSync(this.dir, { recursive: true });
    this.manifestPath = join(this.dir, MANIFEST_FILE);
    this.manifest = this.loadManifest();
  }

  saveBrief(sessionId: string, brief: ResearchBrief): BriefRef {
    const briefId = randomUUID().slice(0, 12);
    const ref: BriefRef = {
      briefId,
      sessionId,
      goal: brief.goal,
      createdAt: new Date().toISOString(),
      findingCount: brief.findings.length,
    };
    writeFileSync(join(this.dir, `${briefId}.json`), JSON.stringify(brief.toJSON(), null, 2), "utf-8");
    this.manifest.push(ref);
    this.flushManifest();
    return ref;
  }

  loadBrief(briefId: string): ResearchBrief | null {
    const path = join(this.dir, `${briefId}.json`);
    if (!existsSync(path)) return null;
    return ResearchBrief.fromJSON(JSON.parse(readFileSync(path, "utf-8")));
  }

  listBriefs(sessionId: string): BriefRef[] {
    return this.manifest.filter((r) => r.sessionId === sessionId);
  }

  briefCount(): number { return this.manifest.length; }

  deleteBrief(briefId: string): boolean {
    const path = join(this.dir, `${briefId}.json`);
    if (!existsSync(path)) return false;
    unlinkSync(path);
    this.manifest = this.manifest.filter((r) => r.briefId !== briefId);
    this.flushManifest();
    return true;
  }

  private loadManifest(): BriefRef[] {
    if (!existsSync(this.manifestPath)) return [];
    return JSON.parse(readFileSync(this.manifestPath, "utf-8"));
  }

  private flushManifest(): void {
    writeFileSync(this.manifestPath, JSON.stringify(this.manifest, null, 2), "utf-8");
  }
}
